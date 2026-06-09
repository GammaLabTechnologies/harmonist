#!/usr/bin/env python3
"""
verify_integration.py -- objective post-integration audit.

Runs inside a target project AFTER the integration prompt has been
executed. Checks that each of the 11 integration steps actually landed,
rather than trusting the orchestrator's self-report.

Usage:
    python3 harmonist/agents/scripts/verify_integration.py
    python3 harmonist/agents/scripts/verify_integration.py --project /path/to/project
    python3 harmonist/agents/scripts/verify_integration.py --json
    python3 harmonist/agents/scripts/verify_integration.py --quiet    # only print failures

Exit codes:
    0 = every `error`-severity check passed
    1 = one or more errors
    2 = cannot run (no AGENTS.md in project root)
"""

from __future__ import annotations

# === PY-GUARD:BEGIN ===
import sys as _asp_sys
if _asp_sys.version_info < (3, 9):
    _asp_cur = "%d.%d" % (_asp_sys.version_info[0], _asp_sys.version_info[1])
    # Guarded argv[0] FIRST: an empty argv (embedded interpreter) must get
    # the friendly message / JSON below, not an IndexError traceback.
    _asp_argv0 = _asp_sys.argv[0] if _asp_sys.argv else ""
    _asp_sys.stderr.write(
        "harmonist requires Python 3.9+ (found " + _asp_cur + ").\n"
        "Install a modern Python and retry:\n"
        "  macOS:   brew install python@3.12 && hash -r\n"
        "  Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
        "  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0\n"
        "Then:     python3 " + _asp_argv0 + "\n"
    )
    # Cursor hooks read a JSON response from stdout; exiting without one
    # makes Cursor treat the hook as broken and silently drop the whole
    # enforcement layer -- including the fail-closed stop gate. When the
    # guarded script is the hook runner, answer the phase in-protocol
    # (shapes match hook_runner.py: emit_allow / "ask" / followup) and
    # exit 0 so the response is honoured. Every other script keeps the
    # plain exit(3).
    _asp_base = _asp_argv0.replace("\\", "/").split("/")[-1]
    if _asp_base == "hook_runner.py":
        _asp_phase = _asp_sys.argv[1] if len(_asp_sys.argv) > 1 else ""
        if _asp_phase == "beforeShellExecution":
            _asp_sys.stdout.write(
                '{"permission": "ask", "user_message": '
                '"harmonist hooks need Python 3.9+ (found ' + _asp_cur + '); '
                'the command safety gate cannot evaluate this command. '
                'Confirm it manually and upgrade python3."}\n'
            )
        elif _asp_phase == "stop":
            _asp_sys.stdout.write(
                '{"followup_message": '
                '"harmonist enforcement hooks need Python 3.9+ (found '
                + _asp_cur + ') and cannot verify the protocol gate '
                '(reviewers / session-handoff are NOT being checked). '
                'Upgrade python3 -- e.g. brew install python@3.12 or '
                'apt install python3.12 -- then retry."}\n'
            )
        else:
            _asp_sys.stdout.write("{}\n")
        _asp_sys.exit(0)
    _asp_sys.exit(3)
# === PY-GUARD:END ===

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

MIN_AGENTS_MD_LINES = 150

# Strict agents every integrated project must carry: derived from
# agents/index.json via the shared strict_slugs module (includes
# wcag-a11y-gate, which the hand-copied list here used to miss).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from strict_slugs import VERIFIED as STRICT_AGENT_SLUGS  # noqa: E402

MIN_SPECIALISTS = 3
MIN_DOMAIN_RULES = 5

# Template sentinels that must NOT remain verbatim in a real project file.
TEMPLATE_MARKERS = [
    "[YOUR PROJECT",
    "[language, framework, ORM",
    "[framework, language, bundler",
    "module-a/",
    "module-b/",
    "[list running services]",
    "[path]",
    "[dev/staging/prod]",
]

# Pack-template Invariants copied verbatim into a project's AGENTS.md.
# If every one of these lines still appears -- in order, unaltered -- the
# project lifted the examples without thinking. Invariants MUST describe
# the project's real non-negotiables, not generic financial-ledger advice.
TEMPLATE_INVARIANT_FINGERPRINT = [
    "No floating-point for money.",
    "State machines: deterministic, idempotent, traceable transitions.",
    "Migrations: append-only. Never modify existing migrations.",
    "Secrets: NEVER log or expose API keys, tokens, passwords, mnemonics.",
    "External calls: require retries, idempotency keys, compensation logic.",
    "Evidence: every risky change leaves tests, logs, metrics, or a verifier report.",
]

# HTML comment that the template uses to tell the integrator "replace
# this block". If any survives into the project file, the orchestrator
# skipped a customization step.
CUSTOMIZE_COMMENT_RE = re.compile(r"<!--\s*CUSTOMIZE\b[^>]*-->", re.IGNORECASE)


@dataclass
class CheckResult:
    name: str
    severity: str       # "error" | "warning" | "info"
    passed: bool
    message: str
    fix: str = ""


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_agents_md_exists(proj: Path) -> CheckResult:
    p = proj / "AGENTS.md"
    if p.exists():
        return CheckResult("agents-md-exists", "error", True, f"{p} present")
    return CheckResult(
        "agents-md-exists", "error", False,
        "AGENTS.md missing from project root",
        "Create AGENTS.md using the pack's AGENTS.template.md as a template.",
    )


def check_agents_md_length(proj: Path) -> CheckResult:
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-length", "error", False, "AGENTS.md missing")
    lines = len(p.read_text().splitlines())
    if lines >= MIN_AGENTS_MD_LINES:
        return CheckResult("agents-md-length", "error", True, f"AGENTS.md has {lines} lines")
    return CheckResult(
        "agents-md-length", "error", False,
        f"AGENTS.md has {lines} lines; expected >= {MIN_AGENTS_MD_LINES}",
        "Re-run integration Step 3 -- the template has ~300 lines, the generated file "
        "should cover every section. The orchestrator likely elided whole blocks.",
    )


def check_agents_md_customized(proj: Path) -> CheckResult:
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-customized", "error", False, "AGENTS.md missing")
    text = p.read_text()
    leaked = [m for m in TEMPLATE_MARKERS if m in text]
    if not leaked:
        return CheckResult("agents-md-customized", "error", True,
                           "AGENTS.md has no raw template placeholders")
    return CheckResult(
        "agents-md-customized", "error", False,
        f"AGENTS.md still contains template placeholders: {leaked[:3]}",
        "Replace [CUSTOMIZE] markers with real values for Platform Stack, Modules, "
        "Invariants. The integration prompt (Step 3) lists every section that must "
        "be concrete.",
    )


def check_agents_md_invariants_customized(proj: Path) -> CheckResult:
    """Detect the failure mode where the integration prompt copied
    AGENTS.md with template Invariants intact. The pack's invariants
    ("No floating-point for money", "Migrations: append-only"...) are
    EXAMPLES -- a real project's Invariants describe its actual
    non-negotiables.

    Fingerprint: if every one of the 6 template lines appears verbatim,
    the section was not customized. If even one was meaningfully
    edited / removed / replaced, we assume the integrator understood
    the intent.
    """
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-invariants", "error", False, "AGENTS.md missing")
    text = p.read_text()
    matched = sum(1 for line in TEMPLATE_INVARIANT_FINGERPRINT if line in text)
    if matched < len(TEMPLATE_INVARIANT_FINGERPRINT):
        return CheckResult(
            "agents-md-invariants", "error", True,
            f"Invariants customized ({matched}/{len(TEMPLATE_INVARIANT_FINGERPRINT)} "
            f"template lines remain -- section was edited)",
        )
    return CheckResult(
        "agents-md-invariants", "error", False,
        f"AGENTS.md Invariants section is the template verbatim "
        f"(all {matched} example lines present).",
        "Replace the Invariants list with rules that are actually non-negotiable "
        "for THIS project. 'No floating-point for money' belongs only in projects "
        "that handle money; 'Migrations: append-only' belongs only where a "
        "migration tool exists. Generic Invariants = no Invariants.",
    )


def check_agents_md_customize_comments(proj: Path) -> CheckResult:
    """The template carries <!-- CUSTOMIZE: ... --> comments that mark
    every section requiring project-specific content. Any that survive
    into a project's AGENTS.md mean the integrator skipped that block
    without even acknowledging it.
    """
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-customize-comments", "error", False,
                           "AGENTS.md missing")
    text = p.read_text()
    # Strip pack-owned blocks -- those can legitimately carry the
    # marker in examples-in-comments; only project-owned prose matters.
    project_text = re.sub(
        r"<!--\s*pack-owned:begin[^>]*-->.*?<!--\s*pack-owned:end\s*-->",
        "", text, flags=re.DOTALL,
    )
    leftover = CUSTOMIZE_COMMENT_RE.findall(project_text)
    if not leftover:
        return CheckResult("agents-md-customize-comments", "error", True,
                           "no leftover CUSTOMIZE markers in project-owned prose")
    return CheckResult(
        "agents-md-customize-comments", "error", False,
        f"AGENTS.md still has {len(leftover)} unresolved CUSTOMIZE comment(s)",
        "Each <!-- CUSTOMIZE: ... --> in the template marks a block that must be "
        "replaced with project-specific content before the comment is removed. "
        "Delete the comment AND fill in the section below it.",
    )


def check_agents_md_references_index(proj: Path) -> CheckResult:
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-index-ref", "error", False, "AGENTS.md missing")
    text = p.read_text()
    if "agents/index.json" in text:
        return CheckResult("agents-md-index-ref", "error", True,
                           "AGENTS.md references agents/index.json for routing")
    return CheckResult(
        "agents-md-index-ref", "error", False,
        "AGENTS.md does not reference agents/index.json -- routing is hard-coded",
        "Add a section pointing at harmonist/agents/index.json as the "
        "orchestrator's routing table. See the template.",
    )


def check_cursor_agents_dir(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "agents"
    if p.exists() and p.is_dir():
        return CheckResult("cursor-agents-dir", "error", True, ".cursor/agents/ exists")
    return CheckResult(
        "cursor-agents-dir", "error", False,
        ".cursor/agents/ missing",
        "Create the directory and copy orchestration + review agents from the pack.",
    )


def _installed_slugs(proj: Path) -> set[str]:
    p = proj / ".cursor" / "agents"
    if not p.exists():
        return set()
    slugs: set[str] = set()
    for md in p.rglob("*.md"):
        stem = md.stem
        # Also read the `name:` frontmatter to catch title-case persona files.
        try:
            text = md.read_text()
        except Exception:
            continue
        slugs.add(stem)
        m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if m:
            slugs.add(m.group(1).strip().lower().replace(" ", "-"))
    return slugs


def check_strict_agents_installed(proj: Path) -> CheckResult:
    installed = _installed_slugs(proj)
    missing = sorted(STRICT_AGENT_SLUGS - installed)
    if not missing:
        return CheckResult("strict-agents-installed", "error", True,
                           f"all {len(STRICT_AGENT_SLUGS)} orchestration+review agents present")
    return CheckResult(
        "strict-agents-installed", "error", False,
        f"missing strict agents in .cursor/agents/: {missing}",
        "Copy each missing file from "
        "harmonist/agents/{orchestration,review}/ into .cursor/agents/.",
    )


def check_specialists_count(proj: Path) -> CheckResult:
    installed = _installed_slugs(proj) - STRICT_AGENT_SLUGS
    if len(installed) >= MIN_SPECIALISTS:
        return CheckResult("specialists-count", "warning", True,
                           f"{len(installed)} specialist(s) installed in .cursor/agents/")
    return CheckResult(
        "specialists-count", "warning", False,
        f"only {len(installed)} specialist(s) installed; expected >= {MIN_SPECIALISTS}",
        "Integration Step 5 should have installed 3-10 specialists from the catalog. "
        "Query agents/index.json by project tags and copy the matches.",
    )


def check_bg_regression_customized(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "agents" / "bg-regression-runner.md"
    if not p.exists():
        return CheckResult(
            "bg-regression-customized", "error", False,
            "bg-regression-runner.md missing",
            "Copy from harmonist/agents/review/ and replace the test/lint/build "
            "commands with this project's actual commands.",
        )
    text = p.read_text()
    # Heuristic: if the file still mentions generic placeholder tokens, it hasn't
    # been localised.
    placeholder_indicators = ["<PROJECT_TEST_CMD>", "<replace this>", "TBD",
                              "your project's test command", "[CUSTOMIZE"]
    leaked = [ind for ind in placeholder_indicators if ind.lower() in text.lower()]
    if leaked:
        return CheckResult(
            "bg-regression-customized", "error", False,
            f"bg-regression-runner.md still contains placeholders: {leaked}",
            "Replace placeholder test/lint/build commands with real project commands.",
        )
    # Additional heuristic: does it name a concrete runner?
    known_runners = ["pytest", "jest", "go test", "cargo test", "mvn test", "./gradlew",
                     "npm test", "yarn", "pnpm", "phpunit", "rspec", "mix test",
                     "ctest", "bazel test"]
    if not any(r in text for r in known_runners):
        return CheckResult(
            "bg-regression-customized", "warning", False,
            "bg-regression-runner.md does not reference any known test runner",
            "Confirm that the body contains the exact test / lint / build commands "
            "this project uses. Hooks invoke these -- if wrong the gate silently no-ops.",
        )
    return CheckResult("bg-regression-customized", "error", True,
                       "bg-regression-runner.md looks project-specific")


def check_memory_setup(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "memory"
    required = ["session-handoff.md", "decisions.md", "patterns.md", "memory.py", "validate.py"]
    missing = [f for f in required if not (p / f).exists()]
    if missing:
        return CheckResult(
            "memory-setup", "error", False,
            f".cursor/memory/ is missing: {missing}",
            "Copy harmonist/memory/* into .cursor/memory/ (CLI + templates).",
        )
    return CheckResult("memory-setup", "error", True, ".cursor/memory/ fully set up")


def check_memory_not_template(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "memory" / "session-handoff.md"
    if not p.exists():
        return CheckResult("memory-not-template", "error", False,
                           "session-handoff.md missing")
    text = p.read_text()
    # If ONLY the 0-0 placeholder exists, no real entry was appended yet.
    has_template = "id: 0-0-state" in text
    real_entry = re.search(r"^id:\s*\d{4,}-\d+-state\s*$", text, re.MULTILINE)
    if real_entry:
        return CheckResult(
            "memory-not-template", "error", True,
            "session-handoff.md has at least one real state entry",
        )
    if has_template:
        return CheckResult(
            "memory-not-template", "error", False,
            "session-handoff.md only has the `0-0-state` template entry; "
            "no real project state recorded",
            "Run: python3 .cursor/memory/memory.py append "
            "--file session-handoff --kind state --status done "
            "--summary '<one line>' --body '<services / recent changes / issues>'",
        )
    return CheckResult(
        "memory-not-template", "error", False,
        "session-handoff.md has no recognizable entries",
        "Append at least one entry via memory.py as above.",
    )


def check_memory_validates(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "memory"
    if not (p / "validate.py").exists():
        return CheckResult("memory-validates", "error", False,
                           "validate.py missing", "See 'memory-setup' fix.")
    rc = subprocess.run(
        [sys.executable, str(p / "validate.py"), "--path", str(p), "--quiet"],
        capture_output=True, text=True,
    )
    if rc.returncode == 0:
        return CheckResult("memory-validates", "error", True,
                           "all memory files pass schema validation")
    return CheckResult(
        "memory-validates", "error", False,
        f"memory files FAILED validation:\n{rc.stderr.strip()[:400]}",
        "Fix the reported issues in the listed memory entries, or regenerate "
        "via memory.py append.",
    )


def check_hooks_installed(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "hooks.json"
    if not p.exists():
        return CheckResult("hooks-json", "error", False,
                           ".cursor/hooks.json missing",
                           "Copy harmonist/hooks/hooks.json to .cursor/hooks.json.")
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return CheckResult("hooks-json", "error", False,
                           f"hooks.json is not valid JSON: {e}",
                           "Replace with the pack's template.")
    declared = set((data.get("hooks") or {}).keys())
    expected = {"sessionStart", "afterFileEdit", "subagentStart",
                "subagentStop", "beforeShellExecution", "stop"}
    missing = sorted(expected - declared)
    if missing:
        return CheckResult("hooks-json", "error", False,
                           f"hooks.json missing events: {missing}",
                           "Merge the pack's hooks.json (re-run upgrade.py "
                           "--apply); do not drop unrelated hooks if they exist.")
    return CheckResult("hooks-json", "error", True,
                       "hooks.json declares all 6 enforcement events")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pack_manifest_drift(proj: Path) -> CheckResult:
    """Supply-chain integrity. .cursor/pack-manifest.json records the sha256
    of every pack-owned file the installer placed. We recompute and compare,
    so post-install tampering -- e.g. weakening gate-stop.sh, hook_runner.py,
    or security-reviewer.md after integration -- is DETECTED here instead of
    passing verification clean. (This is the check the pack-manifest was
    written for; without it the supply-chain promise was hollow.)"""
    mf = proj / ".cursor" / "pack-manifest.json"
    if not mf.exists():
        return CheckResult(
            "pack-manifest-drift", "warning", True,
            "no .cursor/pack-manifest.json -- drift detection unavailable",
            "Run: python3 harmonist/agents/scripts/upgrade.py --apply  "
            "(records sha256 of pack-owned files so later tampering is "
            "detectable).",
        )
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        files = data.get("files") or {}
    except Exception as e:
        return CheckResult(
            "pack-manifest-drift", "error", False,
            f"pack-manifest.json is unreadable ({e.__class__.__name__})",
            "Re-run upgrade.py --apply to rewrite the manifest.",
        )
    if not isinstance(files, dict) or not files:
        return CheckResult(
            "pack-manifest-drift", "warning", True,
            "pack-manifest.json records no file hashes",
            "Run upgrade.py --apply to populate it.",
        )
    missing: list[str] = []
    drifted: list[str] = []
    for rel, expected in files.items():
        fp = proj / rel
        if not fp.exists():
            missing.append(rel)
            continue
        try:
            if _sha256_file(fp) != expected:
                drifted.append(rel)
        except Exception:
            drifted.append(rel)
    if not missing and not drifted:
        return CheckResult(
            "pack-manifest-drift", "error", True,
            f"all {len(files)} pack-owned files match the recorded manifest",
        )
    problems: list[str] = []
    if drifted:
        problems.append(f"{len(drifted)} modified (e.g. {sorted(drifted)[:5]})")
    if missing:
        problems.append(f"{len(missing)} missing (e.g. {sorted(missing)[:5]})")
    return CheckResult(
        "pack-manifest-drift", "error", False,
        "pack-owned files drifted from the recorded manifest: "
        + "; ".join(problems),
        "These files are managed by the pack and must not be edited in place "
        "(it weakens enforcement and breaks predictable upgrades). Restore "
        "them: python3 harmonist/agents/scripts/upgrade.py --apply  -- or, if "
        "a change was intentional, re-run the installer to re-record the "
        "manifest.",
    )


def check_hook_scripts(proj: Path) -> CheckResult:
    scripts_dir = proj / ".cursor" / "hooks" / "scripts"
    # hook_runner.py is the cross-platform active path that hooks.json
    # invokes on every OS (incl. native Windows, which has no bash).
    runner = scripts_dir / "hook_runner.py"
    if not runner.exists():
        return CheckResult(
            "hook-scripts", "error", False,
            "hook_runner.py missing -- the cross-platform hook runner that "
            "hooks.json invokes is not installed",
            "Run: python3 harmonist/agents/scripts/upgrade.py --apply",
        )
    # The POSIX .sh scripts back hooks.posix.json and the shell test
    # harness; the installer ships them on every OS for parity.
    sh_scripts = ["lib.sh", "seed-session.sh", "record-write.sh",
                  "record-subagent-start.sh", "record-subagent-stop.sh",
                  "gate-stop.sh", "gate-shell.sh"]
    missing = [f for f in sh_scripts if not (scripts_dir / f).exists()]
    if missing:
        return CheckResult("hook-scripts", "error", False,
                           f"hook scripts missing: {missing}",
                           "Copy harmonist/hooks/scripts/* into .cursor/hooks/scripts/.")
    # The executable bit only matters where the .sh path can run; on
    # Windows it is meaningless (hooks run via hook_runner.py).
    if os.name != "nt":
        not_exec = [f for f in sh_scripts if not (scripts_dir / f).stat().st_mode & 0o111]
        if not_exec:
            return CheckResult("hook-scripts", "warning", False,
                               f"hook scripts not executable: {not_exec}",
                               "chmod +x .cursor/hooks/scripts/*.sh")
    return CheckResult("hook-scripts", "error", True,
                       "hook_runner.py + 7 shell scripts present")


def check_cursor_rules(proj: Path) -> CheckResult:
    rules_dir = proj / ".cursor" / "rules"
    prot = rules_dir / "protocol-enforcement.mdc"
    domain = rules_dir / "project-domain-rules.mdc"
    failures: list[str] = []
    if not prot.exists():
        failures.append("protocol-enforcement.mdc missing")
    else:
        text = prot.read_text()
        if "alwaysApply: true" not in text:
            failures.append("protocol-enforcement.mdc missing alwaysApply: true")
        if "pack-owned: protocol-enforcement" not in text:
            failures.append(
                "protocol-enforcement.mdc missing pack-owned marker "
                "(<!-- pack-owned: protocol-enforcement v1 -->); "
                "cannot safely refresh on upgrade. "
                "Replace with harmonist/agents/templates/rules/"
                "protocol-enforcement.mdc"
            )
    if not domain.exists():
        failures.append("project-domain-rules.mdc missing")
    else:
        text = domain.read_text()
        if "alwaysApply: true" not in text:
            failures.append("project-domain-rules.mdc missing alwaysApply: true")
        # Count bullets (- ... or 1. ...) in the body.
        body = text.split("---", 2)[-1] if "---" in text else text
        bullets = re.findall(r"^\s*(?:[-*]|\d+\.)\s+\S", body, re.MULTILINE)
        if len(bullets) < MIN_DOMAIN_RULES:
            failures.append(
                f"project-domain-rules.mdc has {len(bullets)} rule bullets; expected >= {MIN_DOMAIN_RULES}"
            )
    if failures:
        return CheckResult(
            "cursor-rules", "error", False,
            " | ".join(failures),
            "Run integration Step 7 for Cursor Rules. Both rule files need "
            "'alwaysApply: true' in their frontmatter and domain-rules needs >=5 concrete rules.",
        )
    return CheckResult("cursor-rules", "error", True,
                       "protocol-enforcement.mdc + project-domain-rules.mdc present and alwaysApply")


def check_agents_md_markers(proj: Path) -> CheckResult:
    p = proj / "AGENTS.md"
    if not p.exists():
        return CheckResult("agents-md-markers", "warning", False, "AGENTS.md missing")
    text = p.read_text()
    begin_count = text.count("<!-- pack-owned:begin")
    end_count = text.count("<!-- pack-owned:end")
    if begin_count == 0:
        return CheckResult(
            "agents-md-markers", "warning", False,
            "AGENTS.md has no pack-owned markers -- future `upgrade.py` cannot "
            "merge pack updates cleanly",
            "Run a one-time bootstrap: copy the pack's AGENTS.template.md again, then "
            "re-apply your Platform Stack / Modules / Invariants / Resilience "
            "customisations between the marker blocks.",
        )
    if begin_count != end_count:
        return CheckResult(
            "agents-md-markers", "error", False,
            f"pack-owned markers unbalanced: {begin_count} begin vs {end_count} end",
            "Run `python3 harmonist/agents/scripts/merge_agents_md.py "
            "--pack harmonist --project .` to inspect and re-apply.",
        )
    return CheckResult(
        "agents-md-markers", "warning", True,
        f"AGENTS.md carries {begin_count} pack-owned marker block(s)",
    )


def check_pack_version_recorded(proj: Path) -> CheckResult:
    p = proj / ".cursor" / "pack-version.json"
    if not p.exists():
        return CheckResult(
            "pack-version-recorded", "warning", False,
            ".cursor/pack-version.json missing; upgrades can't track what version this project is on",
            "Run: python3 harmonist/agents/scripts/upgrade.py --apply   "
            "(it writes the file on success).",
        )
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return CheckResult(
            "pack-version-recorded", "warning", False,
            f"pack-version.json not valid JSON: {e}",
            "Delete the file and re-run upgrade.py --apply.",
        )
    if not data.get("pack_version"):
        return CheckResult(
            "pack-version-recorded", "warning", False,
            "pack-version.json has no `pack_version` field",
            "Re-run upgrade.py --apply to refresh it.",
        )
    return CheckResult(
        "pack-version-recorded", "warning", True,
        f"pack-version.json records version {data['pack_version']!r}",
    )


def check_gitignore_memory(proj: Path) -> CheckResult:
    gi = proj / ".gitignore"
    memory_present = (proj / ".cursor" / "memory").exists()
    # Severity is error iff memory is actually installed; otherwise warning.
    severity = "error" if memory_present else "warning"

    if not gi.exists():
        return CheckResult(
            "gitignore-memory", severity, False,
            ".gitignore missing; memory contents may leak to git",
            "Run 'python3 harmonist/agents/scripts/upgrade.py --apply' -- "
            "it writes the memory-privacy block automatically.",
        )
    text = gi.read_text()
    if "harmonist: memory privacy" in text:
        return CheckResult("gitignore-memory", severity, True,
                           ".gitignore carries the pack's memory-privacy block")
    if ".cursor/memory" in text or "cursor/memory/" in text:
        return CheckResult(
            "gitignore-memory", "warning", True,
            ".gitignore excludes .cursor/memory (legacy form without pack marker)",
        )
    return CheckResult(
        "gitignore-memory", severity, False,
        ".gitignore does not exclude .cursor/memory -- session-sensitive state may leak to git",
        "Run 'python3 harmonist/agents/scripts/upgrade.py --apply' to add the block.",
    )


def _locate_pack_dir(proj: Path) -> Path | None:
    """Find the pack checkout near the project. The pack dir may carry ANY
    name (not just `harmonist/`): prefer the pack this script itself lives
    in, then scan the project root's immediate subdirectories and the
    parent's for a dir with `agents/index.json` AND
    `agents/scripts/check_pack_health.py`."""
    candidates: list[Path] = [Path(__file__).resolve().parent.parent.parent]
    for base in (proj, proj.parent):
        try:
            candidates.extend(sorted(p for p in base.iterdir() if p.is_dir()))
        except OSError:
            continue
    for c in candidates:
        if (c / "agents" / "index.json").exists() and \
                (c / "agents" / "scripts" / "check_pack_health.py").exists():
            return c
    return None


def _locate_pack_script(proj: Path, name: str) -> Path | None:
    pack = _locate_pack_dir(proj)
    if pack is None:
        return None
    script = pack / "agents" / "scripts" / name
    return script if script.exists() else None


def check_rules_conflicts(proj: Path) -> CheckResult:
    """Run scan_rules_conflicts.py against the project's
    .cursor/rules/. Fails on any error-severity finding (directives
    that would subvert protocol-enforcement, missing pack-owned
    marker, duplicate canonical files)."""
    rules_dir = proj / ".cursor" / "rules"
    if not rules_dir.exists():
        return CheckResult(
            "rules-conflicts", "warning", False,
            ".cursor/rules/ missing -- no rules installed",
            "Re-run integration step 8 to install "
            "protocol-enforcement.mdc + project-domain-rules.mdc.",
        )
    scanner = _locate_pack_script(proj, "scan_rules_conflicts.py")
    if scanner is None:
        return CheckResult(
            "rules-conflicts", "warning", False,
            "scan_rules_conflicts.py not found next to project",
        )
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, str(scanner), "--project", str(proj), "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return CheckResult(
            "rules-conflicts", "warning", False,
            f"scanner crashed: {e}",
        )
    try:
        data = json.loads(r.stdout or "{}")
        err = int(data.get("counts", {}).get("error", 0))
        warn = int(data.get("counts", {}).get("warn", 0))
    except Exception:
        err = 1 if r.returncode else 0
        warn = 0
    if r.returncode == 0:
        return CheckResult(
            "rules-conflicts", "info", True,
            f".cursor/rules/ has no enforcement conflicts "
            f"(warnings={warn})",
        )
    return CheckResult(
        "rules-conflicts", "error", False,
        f"{err} rule conflict(s) detected in .cursor/rules/",
        f"Inspect: python3 {scanner} --project {proj}   "
        "Each finding comes with a FIX hint.",
    )


def check_installed_agent_safety(proj: Path) -> CheckResult:
    """Scan every installed agent in .cursor/agents/ for prompt-injection
    patterns. An attacker (or careless contributor) can drop a hostile
    agent body into an open-source project; this is the post-install
    version of the pack's own catalog scan."""
    adir = proj / ".cursor" / "agents"
    if not adir.exists():
        return CheckResult(
            "installed-agent-safety", "warning", False,
            ".cursor/agents/ missing -- nothing to scan",
        )
    # Locate scan_agent_safety.py -- the pack (any dir name) is typically a
    # subdirectory or sibling of proj.
    scanner = _locate_pack_script(proj, "scan_agent_safety.py")
    if scanner is None:
        return CheckResult(
            "installed-agent-safety", "warning", False,
            "scan_agent_safety.py not found next to the project",
            "Ensure the pack is a sibling / subdirectory of the "
            "project and re-run.",
        )
    import subprocess  # local import; verify_integration is otherwise stdlib-only
    try:
        r = subprocess.run(
            [sys.executable, str(scanner), "--project", str(proj), "--json"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        return CheckResult(
            "installed-agent-safety", "warning", False,
            f"scanner crashed: {e}",
            f"Run directly: python3 {scanner} --project {proj} -v",
        )
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout or "{}")
            warn = int(data.get("counts", {}).get("warn", 0))
            n = len(data.get("findings") or [])
            tail = f" ({warn} warn-level)" if warn else ""
            return CheckResult(
                "installed-agent-safety", "info", True,
                f"{n} finding(s); no prompt-injection / exfil errors{tail}",
            )
        except Exception:
            return CheckResult(
                "installed-agent-safety", "info", True,
                "scanner exited clean",
            )
    try:
        data = json.loads(r.stdout or "{}")
        n = data.get("counts", {}).get("error", "?")
    except Exception:
        n = "?"
    return CheckResult(
        "installed-agent-safety", "error", False,
        f"{n} prompt-injection / exfil finding(s) in .cursor/agents/",
        f"Inspect: python3 {scanner} --project {proj} -v   "
        "then review and delete any hostile agent files before using the project.",
    )


CHECKS = [
    check_agents_md_exists,
    check_agents_md_length,
    check_agents_md_customized,
    check_agents_md_invariants_customized,
    check_agents_md_customize_comments,
    check_agents_md_references_index,
    check_cursor_agents_dir,
    check_strict_agents_installed,
    check_specialists_count,
    check_bg_regression_customized,
    check_memory_setup,
    check_memory_not_template,
    check_memory_validates,
    check_hooks_installed,
    check_hook_scripts,
    check_cursor_rules,
    check_agents_md_markers,
    check_pack_version_recorded,
    check_pack_manifest_drift,
    check_gitignore_memory,
    check_installed_agent_safety,
    check_rules_conflicts,
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all(proj: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in CHECKS:
        try:
            results.append(check(proj))
        except Exception as e:
            results.append(CheckResult(
                name=check.__name__.replace("check_", ""),
                severity="error", passed=False,
                message=f"check crashed: {e.__class__.__name__}: {e}",
                fix="Report this traceback as a bug in the pack.",
            ))
    return results


def render_text(results: list[CheckResult], quiet: bool) -> str:
    lines: list[str] = []
    for r in results:
        if quiet and r.passed:
            continue
        icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}.get(r.severity, "?") if not r.passed else "✓"
        lines.append(f"  {icon}  [{r.severity:7s}] {r.name}: {r.message}")
        if not r.passed and r.fix:
            for fl in r.fix.splitlines():
                lines.append(f"       FIX: {fl}")
    errors = sum(1 for r in results if r.severity == "error" and not r.passed)
    warnings = sum(1 for r in results if r.severity == "warning" and not r.passed)
    passed = sum(1 for r in results if r.passed)
    lines.append("")
    lines.append(f"  Summary: {passed}/{len(results)} passed, "
                 f"{errors} error(s), {warnings} warning(s).")
    lines.append("  OK" if errors == 0 else "  FAILED -- fix the errors above before proceeding.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project root to audit. Default: current directory.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    ap.add_argument("--quiet", action="store_true", help="Only print non-passing checks.")
    args = ap.parse_args(argv)

    proj = args.project.resolve()
    if not (proj / "AGENTS.md").exists() and not args.json:
        print(f"verify_integration: no AGENTS.md in {proj}.", file=sys.stderr)
        print("Run integration first (see harmonist/integration-prompt.md).", file=sys.stderr)
        return 2

    results = run_all(proj)

    if args.json:
        payload = {
            "project": str(proj),
            "results": [asdict(r) for r in results],
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r.passed),
                "errors": sum(1 for r in results if r.severity == "error" and not r.passed),
                "warnings": sum(1 for r in results if r.severity == "warning" and not r.passed),
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(results, args.quiet))

    errors = sum(1 for r in results if r.severity == "error" and not r.passed)
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
