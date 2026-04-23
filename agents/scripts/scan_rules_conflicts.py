#!/usr/bin/env python3
"""
scan_rules_conflicts.py -- detect conflicts in a project's
`.cursor/rules/*.mdc` set.

Cursor Rules with `alwaysApply: true` all land in the model's context.
If two of them give opposite guidance ("always run qa-verifier" vs
"skip qa-verifier for hotfixes"), the orchestrator sees both and the
outcome is unpredictable. This scanner codifies five classes of
conflict:

1. **pack-marker-missing**: a `protocol-enforcement.mdc` exists but
   lacks the `<!-- pack-owned: ... -->` marker. It's either a
   pre-existing user file about to be silently overwritten, or a
   stale pre-v1 pack copy. Either way, ambiguous.

2. **protocol-contradiction**: any `.mdc` body contains a directive
   that negates an enforcement invariant (e.g. "skip qa-verifier",
   "never run security-reviewer", "disable the stop hook"). Project
   rules are allowed to add restrictions but not subtract them.

3. **alwaysApply-overload**: more than N (default 5) `.mdc` files
   carry `alwaysApply: true`. Too many always-apply rules eat context
   and raise the chance of mutual contradiction.

4. **phantom-slug-reference**: a rule references an agent slug
   (`qa-verifier`, `backend-architect`, etc.) that isn't installed
   under `.cursor/agents/`. When the orchestrator tries to follow the
   rule it will fail.

5. **duplicate-purpose**: two different file names cover the same
   topic (e.g. `protocol.mdc` and `protocol-enforcement.mdc`, or
   `security.mdc` and `security-rules.mdc`). Readers have to guess
   which wins.

Usage:
    python3 agents/scripts/scan_rules_conflicts.py                 # scan ./.cursor/rules
    python3 agents/scripts/scan_rules_conflicts.py --project /p    # scan /p/.cursor/rules
    python3 agents/scripts/scan_rules_conflicts.py --json          # machine output

Exit codes:
    0  -- no error-severity findings
    1  -- at least one error-severity finding
    2  -- scanner crash / bad input
"""

from __future__ import annotations

# === PY-GUARD:BEGIN ===
import sys as _asp_sys
if _asp_sys.version_info < (3, 9):
    _asp_cur = "%d.%d" % (_asp_sys.version_info[0], _asp_sys.version_info[1])
    _asp_sys.stderr.write(
        "harmonist requires Python 3.9+ (found " + _asp_cur + ").\n"
        "Install a modern Python and retry:\n"
        "  macOS:   brew install python@3.12 && hash -r\n"
        "  Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
        "  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0\n"
        "Then:     python3 " + _asp_sys.argv[0] + "\n"
    )
    _asp_sys.exit(3)
# === PY-GUARD:END ===

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Protocol-contradiction patterns. Each entry = (id, regex, explanation).
# These look for explicit instructions to disable enforcement. Tightened
# so doc-style content ("to skip the build", "bypass the CDN cache")
# doesn't false-positive.
# ---------------------------------------------------------------------------

STRICT_REVIEWERS = [
    "qa-verifier",
    "security-reviewer",
    "code-quality-auditor",
    "sre-observability",
    "bg-regression-runner",
    "repo-scout",
]

_CONTRADICTIONS: list[tuple[str, re.Pattern, str]] = [
    ("skip-strict-reviewer",
     re.compile(
         r"(skip|bypass|do\s*not\s+run|don'?t\s+run|never\s+invoke|ignore|"
         r"avoid\s+(calling|running|invoking))\s+"
         r"(the\s+|any\s+)?"
         r"(?:`)?(qa-verifier|security-reviewer|code-quality-auditor|"
         r"sre-observability|bg-regression-runner)(?:`)?",
         re.IGNORECASE),
     "Rule tells the orchestrator to skip a strict reviewer; "
     "protocol-enforcement requires them."),

    ("approve-without-review",
     re.compile(
         r"(always|automatically|just|silently)\s+(approve|mark\s+(as\s+)?"
         r"complete|sign\s*-?off|green-?light)\s+"
         r"(?:the\s+)?(task|review|pr|change|deliverable)?",
         re.IGNORECASE),
     "Rule authorises blanket auto-approval without review."),

    ("edit-without-delegation",
     re.compile(
         r"(edit|modify|write)\s+(the\s+)?(code|files?)\s+"
         r"(directly|yourself|without\s+delegation|without\s+(a\s+)?subagent)",
         re.IGNORECASE),
     "Rule tells the orchestrator to edit code without delegating; "
     "stop hook will block the turn."),

    ("disable-hook",
     re.compile(
         r"(disable|bypass|turn\s*off|ignore)\s+(the\s+)?"
         r"(stop\s*-?hook|gate|enforcement|protocol)",
         re.IGNORECASE),
     "Rule tells the orchestrator to disable enforcement itself."),

    ("no-memory-update",
     re.compile(
         r"(skip|don'?t|do\s*not|never)\s+"
         r"(update|updating|updates?|append(?:ing)?|write|writing|writes?)"
         r"(?:\s+\w+){0,4}?\s+"
         r"(session-handoff|memory|\.cursor/memory)",
         re.IGNORECASE),
     "Rule tells the orchestrator to skip memory updates; "
     "protocol-enforcement requires them on every task with writes."),

    ("omit-agent-marker",
     re.compile(
         r"(skip|don'?t|do\s*not|omit)\s+(adding|writing|including|emitting)\s+"
         r"(the\s+)?(AGENT:|agent\s+marker|subagent\s+marker)",
         re.IGNORECASE),
     "Rule tells the orchestrator to omit the AGENT: marker; "
     "hooks cannot credit the reviewer without it."),
]


DUPLICATE_PAIRS: list[tuple[str, str]] = [
    # (canonical, duplicate) — the canonical should be kept, duplicate is noise.
    ("protocol-enforcement", "protocol"),
    ("protocol-enforcement", "enforcement"),
    ("protocol-enforcement", "orchestration-rules"),
    ("protocol-enforcement", "agent-protocol"),
    ("project-domain-rules", "domain-rules"),
    ("project-domain-rules", "domain"),
    ("project-domain-rules", "project-rules"),
]


MARKER_RE = re.compile(
    r"<!--\s*pack-owned:\s*protocol-enforcement\b", re.IGNORECASE)

ALWAYS_APPLY_RE = re.compile(
    r"^alwaysApply:\s*(true|True|yes)\s*$", re.MULTILINE)

FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)

# Match any mention of an installed-agent slug in rule body.
SLUG_RE = re.compile(r"\b([a-z][a-z0-9-]{2,})\b")


@dataclass
class Finding:
    rule: str
    severity: str  # error | warn | info
    file: str
    message: str
    fix: str = ""


def _read_mdc(path: Path) -> tuple[str, str]:
    """Return (frontmatter_text, body_text)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return ("", text)
    return (m.group(1), text[m.end():])


def _installed_slugs(proj: Path) -> set[str]:
    adir = proj / ".cursor" / "agents"
    if not adir.exists():
        return set()
    return {p.stem for p in adir.rglob("*.md")}


def _alwaysApply(fm: str) -> bool:
    return bool(ALWAYS_APPLY_RE.search(fm))


def _is_pack_layout(proj: Path) -> bool:
    """Heuristic: `proj` points at the pack's own checkout rather than an
    integrated project. Recognised by the presence of files that only
    exist inside the pack source tree and are never copied into
    `.cursor/`. Used to downgrade / suppress findings that only make
    sense in a real project (e.g. "rules-dir-missing" while scanning
    the pack's own repo root)."""
    return (
        (proj / "VERSION").is_file()
        and (proj / "agents" / "index.json").is_file()
        and (proj / "agents" / "scripts" / "check_pack_health.py").is_file()
        and not (proj / ".cursor" / "pack-version.json").exists()
    )


def scan(proj: Path, always_apply_cap: int = 5) -> list[Finding]:
    rules_dir = proj / ".cursor" / "rules"
    if not rules_dir.exists():
        if _is_pack_layout(proj):
            # Running on the pack's own repo: `.cursor/rules/` legitimately
            # does not exist here (it only materialises inside integrated
            # projects). No-op instead of a misleading warning.
            return []
        return [Finding(
            rule="rules-dir-missing", severity="warn",
            file=str(rules_dir),
            message=".cursor/rules/ does not exist",
            fix="Integration step 8 creates it; re-run the integration "
                "prompt if this is expected.",
        )]

    mdc_files = sorted(rules_dir.rglob("*.mdc"))
    if not mdc_files:
        return [Finding(
            rule="rules-empty", severity="warn", file=str(rules_dir),
            message=".cursor/rules/ is empty -- protocol-enforcement missing",
            fix="Copy the canonical template: cp harmonist/"
                "agents/templates/rules/protocol-enforcement.mdc "
                ".cursor/rules/",
        )]

    findings: list[Finding] = []
    installed = _installed_slugs(proj)
    always_apply_files: list[Path] = []
    name_map: dict[str, Path] = {}

    for f in mdc_files:
        fm, body = _read_mdc(f)
        stem = f.stem
        name_map[stem] = f

        if _alwaysApply(fm):
            always_apply_files.append(f)

        # 1. pack-marker check for the canonical file
        if stem == "protocol-enforcement":
            if not MARKER_RE.search(body):
                findings.append(Finding(
                    rule="pack-marker-missing", severity="error",
                    file=str(f),
                    message=(
                        "protocol-enforcement.mdc is missing the "
                        "`<!-- pack-owned: protocol-enforcement v1 -->` "
                        "marker; upgrade.py cannot safely refresh it."),
                    fix=("Replace the file with the canonical template: "
                         f"cp harmonist/agents/templates/rules/"
                         f"protocol-enforcement.mdc {f}"),
                ))

        # 2. protocol contradictions (only check rules that claim alwaysApply;
        #    a one-off note file can say whatever).
        if _alwaysApply(fm):
            for rid, rx, expl in _CONTRADICTIONS:
                # Don't flag the canonical file itself -- it QUOTES these
                # phrases while forbidding them. A simple heuristic: if the
                # line containing the match also contains "NEVER", "must not",
                # or "do not", assume it's a prohibition, not an instruction.
                for m in rx.finditer(body):
                    line_start = body.rfind("\n", 0, m.start()) + 1
                    line_end = body.find("\n", m.end())
                    if line_end == -1:
                        line_end = len(body)
                    line = body[line_start:line_end]
                    if re.search(
                        r"\b(NEVER|must\s+not|do\s+not|forbidden|"
                        r"disallowed|prohibited|don'?t)\b",
                        line, re.IGNORECASE,
                    ):
                        continue
                    findings.append(Finding(
                        rule=rid, severity="error", file=str(f),
                        message=f"{expl}  ({line.strip()[:120]})",
                        fix=("Remove or rephrase the offending directive; "
                             "protocol-enforcement.mdc cannot be overridden "
                             "for enforcement content."),
                    ))
                    break  # one hit per rule per file is enough

        # 4. phantom slug references (skip canonical file: it cites slugs
        #    as examples, not commands).
        if installed and stem != "protocol-enforcement":
            for m in SLUG_RE.finditer(body):
                token = m.group(1)
                if not token.endswith("-reviewer") and "verifier" not in token \
                        and not token.endswith("-auditor") \
                        and not token.endswith("-runner") \
                        and not token.endswith("-scout") \
                        and not token.endswith("-observability"):
                    continue
                # Known strict slug?
                if token in STRICT_REVIEWERS:
                    if token not in installed:
                        findings.append(Finding(
                            rule="phantom-slug-reference",
                            severity="error", file=str(f),
                            message=(f"rule references '{token}' but it is "
                                     f"not installed under .cursor/agents/"),
                            fix=(f"Install via upgrade.py --apply, or remove "
                                 f"the reference from {f.name}."),
                        ))
                        break

    # 3. alwaysApply overload
    if len(always_apply_files) > always_apply_cap:
        findings.append(Finding(
            rule="alwaysApply-overload", severity="warn",
            file=str(rules_dir),
            message=(
                f"{len(always_apply_files)} rules set alwaysApply: true "
                f"(recommended max: {always_apply_cap}). Each adds "
                f"pressure on the context window and the chance of mutual "
                f"contradiction."),
            fix=("Review each and flip non-critical ones to "
                 "`alwaysApply: false` (they'll still apply via "
                 "auto-attached / manual modes)."),
        ))

    # 5. duplicate-purpose stems
    for canonical, duplicate in DUPLICATE_PAIRS:
        if canonical in name_map and duplicate in name_map:
            findings.append(Finding(
                rule="duplicate-purpose", severity="warn",
                file=str(name_map[duplicate]),
                message=(f"'{duplicate}.mdc' seems to duplicate "
                         f"'{canonical}.mdc' in purpose"),
                fix=(f"Consolidate: keep '{canonical}.mdc' (canonical "
                     f"pack name), migrate any project-specific content "
                     f"into '{canonical}.mdc' or 'project-domain-rules.mdc', "
                     f"then delete '{duplicate}.mdc'."),
            ))

    return findings


def render(findings: list[Finding]) -> str:
    if not findings:
        return "  no conflicts found in .cursor/rules/*.mdc."
    lines: list[str] = []
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    lines.append(f"  {len(errors)} error(s), {len(warns)} warning(s):")
    for f in findings:
        marker = {"error": "✖", "warn": "!", "info": "·"}.get(f.severity, "?")
        lines.append(f"  {marker} [{f.rule}] {f.file}")
        lines.append(f"      {f.message}")
        if f.fix:
            lines.append(f"      FIX: {f.fix}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--always-apply-cap", type=int, default=5)
    args = ap.parse_args(argv)

    proj = args.project.resolve()
    if not proj.is_dir():
        print(f"error: {proj} is not a directory", file=sys.stderr)
        return 2

    try:
        findings = scan(proj, always_apply_cap=args.always_apply_cap)
    except Exception as e:
        print(f"scanner crashed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "project": str(proj),
            "findings": [asdict(f) for f in findings],
            "counts": {
                "error": sum(1 for f in findings if f.severity == "error"),
                "warn":  sum(1 for f in findings if f.severity == "warn"),
            },
        }, indent=2))
    else:
        print(render(findings))

    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
