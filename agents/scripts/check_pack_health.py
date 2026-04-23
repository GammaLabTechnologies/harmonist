#!/usr/bin/env python3
"""
check_pack_health.py -- preflight check that the pack itself is in good
shape BEFORE a project relies on it.

Catches the failure mode where a stale clone, a truncated download, or
a local edit leaves the pack half-broken. Integration would otherwise
silently produce a half-broken project.

Run from inside the pack (the default) or point --pack at another copy.

Checks (all fatal unless noted):

  1. VERSION file exists, parses as SemVer X.Y.Z.
  2. CHANGELOG.md exists and is non-trivially sized.
  3. agents/index.json up to date (build_index.py --check).
  4. Agent lint passes (lint_agents.py).
  5. Migrator is idempotent (no pending migrations).
  6. At least MIN_AGENTS agents in the pool (catches truncated clones).
  7. All category folders exist.
  8. Every required script is present AND executable.
  9. hooks/ + memory/ subtrees complete.
 10. tags.json loads and has the declared vocab size.
 11. README / AGENTS.md / GUIDE_*.md / integration-prompt.md claim the
     same total and per-category counts as agents/index.json
     (no stale marketing numbers).

Exit codes:
    0 = pack is healthy
    1 = one or more checks failed (with per-check FIX hints)
    2 = cannot run (not a pack)
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
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

MIN_AGENTS = 100  # current pool is 186; a clone below this is truncated
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$")

REQUIRED_SCRIPTS = [
    "agents/scripts/migrate_schema.py",
    "agents/scripts/build_index.py",
    "agents/scripts/lint_agents.py",
    "agents/scripts/lint-agents.sh",
    "agents/scripts/convert.sh",
    "agents/scripts/install.sh",
    "agents/scripts/detect_clones.py",
    "agents/scripts/extract_essentials.py",
    "agents/scripts/project_context.py",
    "agents/scripts/verify_integration.py",
    "agents/scripts/upgrade.py",
    "agents/scripts/merge_agents_md.py",
    "agents/scripts/scan_memory_leaks.py",
    "agents/scripts/detect_regression_commands.py",
    "agents/scripts/smoke_test.py",
    "agents/scripts/check_pack_health.py",
    "agents/scripts/refresh_py_guard.py",
    "agents/scripts/report_usage.py",
    "agents/scripts/build_manifest.py",
    "agents/scripts/scan_agent_safety.py",
    "agents/scripts/scan_rules_conflicts.py",
    "agents/scripts/scan_agent_freshness.py",
    "agents/scripts/onboard.py",
    "agents/scripts/run_regression.py",
    "agents/scripts/integrate.py",
    "agents/scripts/deintegrate.py",
    "agents/scripts/telemetry_webhook.py",
    "agents/scripts/insert_deep_ref_marker.py",
    "agents/scripts/install_extras.py",
]

REQUIRED_DIRS = [
    "agents",
    "agents/orchestration",
    "agents/review",
    "agents/engineering",
    "agents/design",
    "agents/testing",
    "agents/product",
    "agents/project-management",
    "agents/marketing",
    "agents/paid-media",
    "agents/sales",
    "agents/finance",
    "agents/support",
    "agents/academic",
    "agents/game-development",
    "agents/spatial-computing",
    "agents/specialized",
    "agents/scripts",
    "agents/templates",
    "agents/integrations",
    "hooks/scripts",
    "memory",
    "playbooks",
]

REQUIRED_HOOK_SCRIPTS = [
    "hooks/scripts/lib.sh",
    "hooks/scripts/seed-session.sh",
    "hooks/scripts/record-write.sh",
    "hooks/scripts/record-subagent-start.sh",
    "hooks/scripts/record-subagent-stop.sh",
    "hooks/scripts/gate-stop.sh",
    "hooks/hooks.json",
]

REQUIRED_MEMORY_FILES = [
    "memory/memory.py",
    "memory/validate.py",
    "memory/SCHEMA.md",
    "memory/README.md",
    "memory/session-handoff.md",
    "memory/decisions.md",
    "memory/patterns.md",
]

REQUIRED_TOP_FILES = [
    "VERSION",
    "CHANGELOG.md",
    "AGENTS.md",
    "README.md",
    "integration-prompt.md",
    "agents/SCHEMA.md",
    "agents/TAGS.md",
    "agents/tags.json",
    "agents/index.json",
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    fix: str = ""


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_manifest(pack: Path) -> CheckResult:
    mf = pack / "MANIFEST.sha256"
    if not mf.exists():
        return CheckResult(
            "manifest-present", False,
            "MANIFEST.sha256 missing; pack integrity cannot be verified",
            "Generate with: python3 agents/scripts/build_manifest.py",
        )
    script = pack / "agents" / "scripts" / "build_manifest.py"
    if not script.exists():
        return CheckResult(
            "manifest-present", False,
            "build_manifest.py missing",
            "Restore the pack from a fresh clone.",
        )
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--verify"],
            cwd=pack, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return CheckResult(
            "manifest-present", False,
            f"could not verify manifest: {e}",
            "Try: python3 agents/scripts/build_manifest.py --verify",
        )
    if r.returncode == 0:
        return CheckResult(
            "manifest-present", True,
            "MANIFEST.sha256 matches current pack contents",
        )
    head = (r.stdout or r.stderr).splitlines()[:3]
    return CheckResult(
        "manifest-present", False,
        "MANIFEST.sha256 drift detected: " + " | ".join(head),
        "If the drift is expected (you edited a pack file), regenerate: "
        "python3 agents/scripts/build_manifest.py. "
        "Otherwise treat as supply-chain tampering.",
    )


def check_agent_freshness(pack: Path) -> CheckResult:
    script = pack / "agents" / "scripts" / "scan_agent_freshness.py"
    if not script.exists():
        return CheckResult(
            "agent-freshness", False,
            "scan_agent_freshness.py missing",
            "Restore agents/scripts/scan_agent_freshness.py from the pack.",
        )
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=pack, capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return CheckResult(
            "agent-freshness", False,
            f"freshness scan crashed: {e}",
            "Run: python3 agents/scripts/scan_agent_freshness.py -v",
        )
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout or "{}")
            warns = int(data.get("counts", {}).get("warn", 0))
            return CheckResult(
                "agent-freshness", True,
                f"no deprecated-tech errors (warnings={warns})",
            )
        except Exception:
            return CheckResult(
                "agent-freshness", True,
                "freshness scan exited clean",
            )
    try:
        data = json.loads(r.stdout or "{}")
        n = data.get("counts", {}).get("error", "?")
    except Exception:
        n = "?"
    return CheckResult(
        "agent-freshness", False,
        f"{n} deprecated-tech reference(s) in the catalog",
        "Inspect: python3 agents/scripts/scan_agent_freshness.py",
    )


def check_agent_safety(pack: Path) -> CheckResult:
    script = pack / "agents" / "scripts" / "scan_agent_safety.py"
    if not script.exists():
        return CheckResult(
            "agent-safety-scan", False,
            "scan_agent_safety.py missing",
            "Restore agents/scripts/scan_agent_safety.py from the pack.",
        )
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=pack, capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        return CheckResult(
            "agent-safety-scan", False,
            f"scanner crashed: {e}",
            "Run directly: python3 agents/scripts/scan_agent_safety.py -v",
        )
    if r.returncode == 0:
        try:
            data = json.loads(r.stdout or "{}")
            warn = int(data.get("counts", {}).get("warn", 0))
            return CheckResult(
                "agent-safety-scan", True,
                f"no prompt-injection / exfil patterns (warns={warn})",
            )
        except Exception:
            return CheckResult(
                "agent-safety-scan", True,
                "scanner exited clean",
            )
    try:
        data = json.loads(r.stdout or "{}")
        n = data.get("counts", {}).get("error", "?")
    except Exception:
        n = "?"
    return CheckResult(
        "agent-safety-scan", False,
        f"{n} prompt-injection / exfil finding(s) in the catalog",
        "Inspect: python3 agents/scripts/scan_agent_safety.py",
    )


def check_python_version(pack: Path) -> CheckResult:
    cur = f"{sys.version_info[0]}.{sys.version_info[1]}"
    if sys.version_info < (3, 9):
        return CheckResult(
            "python-version", False,
            f"running under Python {cur}; pack requires 3.9+",
            "macOS: `brew install python@3.12`; Ubuntu: `apt install python3.12`; "
            "then re-run with `python3.12 agents/scripts/check_pack_health.py`.",
        )
    return CheckResult(
        "python-version", True,
        f"running under Python {cur} (minimum: 3.9)",
    )


def check_py_guards_fresh(pack: Path) -> CheckResult:
    refresh = pack / "agents" / "scripts" / "refresh_py_guard.py"
    if not refresh.exists():
        return CheckResult(
            "py-guards-fresh", False,
            "refresh_py_guard.py missing",
            "Restore agents/scripts/refresh_py_guard.py from the pack.",
        )
    try:
        r = subprocess.run(
            [sys.executable, str(refresh), "--check"],
            cwd=pack, capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return CheckResult(
            "py-guards-fresh", False,
            f"could not run refresh_py_guard.py --check: {e}",
            "Try running it manually to see what's wrong.",
        )
    if r.returncode == 0:
        return CheckResult(
            "py-guards-fresh", True,
            "python-version guards are in sync across all entry scripts",
        )
    return CheckResult(
        "py-guards-fresh", False,
        "one or more entry scripts carry a stale python-version guard",
        "Run: python3 agents/scripts/refresh_py_guard.py",
    )


def check_version(pack: Path) -> CheckResult:
    vf = pack / "VERSION"
    if not vf.exists():
        return CheckResult("version-file", False, "VERSION file missing",
                           "Restore the pack from a fresh clone.")
    text = vf.read_text().strip()
    if not SEMVER_RE.match(text):
        return CheckResult("version-file", False,
                           f"VERSION='{text}' does not parse as SemVer",
                           "Fix the file to contain 'X.Y.Z' on a single line.")
    return CheckResult("version-file", True, f"pack version {text}")


def check_changelog(pack: Path) -> CheckResult:
    cf = pack / "CHANGELOG.md"
    if not cf.exists():
        return CheckResult("changelog", False, "CHANGELOG.md missing",
                           "Restore from upstream.")
    if cf.stat().st_size < 200:
        return CheckResult("changelog", False,
                           f"CHANGELOG.md suspiciously small ({cf.stat().st_size} bytes)",
                           "Download a complete copy from the pack's upstream repo.")
    return CheckResult("changelog", True, "CHANGELOG.md present and non-trivial")


def check_index_fresh(pack: Path) -> CheckResult:
    script = pack / "agents" / "scripts" / "build_index.py"
    if not script.exists():
        return CheckResult("index-fresh", False, "build_index.py missing",
                           "Pack is truncated; re-clone.")
    rc = _run(["python3", str(script), "--check"], cwd=pack)
    if rc.returncode == 0:
        return CheckResult("index-fresh", True, "agents/index.json is up to date")
    return CheckResult(
        "index-fresh", False,
        f"agents/index.json is stale: {rc.stderr.strip() or rc.stdout.strip()}",
        "Run: python3 agents/scripts/build_index.py",
    )


def check_lint(pack: Path) -> CheckResult:
    script = pack / "agents" / "scripts" / "lint-agents.sh"
    if not script.exists():
        return CheckResult("lint", False, "lint-agents.sh missing",
                           "Re-clone the pack.")
    rc = _run(["bash", str(script)], cwd=pack)
    if rc.returncode == 0:
        tail = (rc.stdout.splitlines()[-3:] if rc.stdout else [])
        return CheckResult("lint", True, "agent lint passes")
    return CheckResult(
        "lint", False,
        f"agent lint failed:\n{rc.stdout.strip()[:400]}",
        "Run: bash agents/scripts/lint-agents.sh   and fix the reported errors.",
    )


def check_migrator_idempotent(pack: Path) -> CheckResult:
    script = pack / "agents" / "scripts" / "migrate_schema.py"
    if not script.exists():
        return CheckResult("migrator-idempotent", False, "migrate_schema.py missing",
                           "Re-clone.")
    rc = _run(["python3", str(script)], cwd=pack)
    if rc.returncode != 0:
        return CheckResult(
            "migrator-idempotent", False,
            f"migrator crashed: {rc.stderr.strip() or rc.stdout.strip()}",
            "Investigate migrate_schema.py.",
        )
    last = rc.stdout.strip().splitlines()[-1] if rc.stdout else ""
    if "Updated 0 files" in last:
        return CheckResult("migrator-idempotent", True, "migrator is idempotent (0 changes)")
    return CheckResult(
        "migrator-idempotent", False,
        f"migrator reported changes on fresh pack ({last})",
        "Run migrate_schema.py and commit the resulting diff; the pack "
        "is out of sync with its own frontmatter rules.",
    )


def check_agent_count(pack: Path) -> CheckResult:
    idx_path = pack / "agents" / "index.json"
    if not idx_path.exists():
        return CheckResult("agent-count", False,
                           "agents/index.json missing",
                           "Run: python3 agents/scripts/build_index.py")
    try:
        idx = json.loads(idx_path.read_text())
    except Exception as e:
        return CheckResult("agent-count", False,
                           f"index.json not valid JSON: {e}",
                           "Regenerate via build_index.py.")
    total = int(idx.get("counts", {}).get("total", 0))
    if total < MIN_AGENTS:
        return CheckResult(
            "agent-count", False,
            f"only {total} agents in the pool (expected >= {MIN_AGENTS})",
            "This clone is truncated. Pull a complete copy from upstream.",
        )
    return CheckResult("agent-count", True, f"{total} agents in the pool")


# Files that advertise the agent catalog size to humans. If any of them
# claims a count that disagrees with agents/index.json, treat it as
# stale marketing -- the kind of drift that made "175 agents" ship in
# a pack that already had 186.
_COUNT_CLAIM_FILES = [
    "README.md",
    "AGENTS.md",
    "GUIDE_EN.md",
    "integration-prompt.md",
    "agents/README.md",
    "CHANGELOG.md",
]

# Patterns that capture an explicit total claim, e.g. "186 agents",
# "186-agent unified catalog", "catalog of 186 agents". Each regex
# must expose ONE capturing group with the number.
_TOTAL_CLAIM_PATTERNS = [
    re.compile(r"\*\*(\d+)\s+agents?\*\*"),
    re.compile(r"catalog of (\d+)\s+agents?", re.IGNORECASE),
    re.compile(r"(\d+)-agent\s+unified\s+catalog", re.IGNORECASE),
    re.compile(r"routing table:?\s*\((\d+)\s+agents?\)", re.IGNORECASE),
    re.compile(r"routing table\s*\((\d+)\s+agents?\)", re.IGNORECASE),
    re.compile(r"\((\d+)\s+agents?,\s*\d+\s+categor", re.IGNORECASE),
    re.compile(r"(\d+)\s+agents?\s+in\s+(?:a\s+)?(?:single\s+)?(?:unified\s+)?(?:pool|one pool|catalog)", re.IGNORECASE),
    re.compile(r"(\d+)\s+entries", re.IGNORECASE),
    re.compile(r"Agent categories \((\d+)\s+total\)", re.IGNORECASE),
]

# Per-category table rows, e.g.:
#   | `engineering` | 46 | persona | ... |
#   | `engineering` | Backend, ... | persona | 46 |
# We accept either position for the number and verify against counts.by_category.
_CATEGORY_ROW_RE = re.compile(
    r"^\|\s*`(?P<cat>[a-z][a-z0-9-]*)`\s*\|"
    r"(?P<rest>.+?)\|\s*$"
)

# GUIDE_*.md use display-name tables (no backticks around the slug), e.g.:
#   | Orchestration | 2 | `repo-scout` |
#   | Game development | 20 | Unity, Unreal... |
# Maps a display name (case-insensitive, trimmed) to the canonical slug in
# index.json's counts.by_category. Kept in sync with the 16 categories.
_DISPLAY_TO_SLUG = {
    "orchestration":       "orchestration",
    "review":              "review",
    "engineering":         "engineering",
    "design":              "design",
    "testing":             "testing",
    "product":             "product",
    "project management":  "project-management",
    "project-management":  "project-management",
    "marketing":           "marketing",
    "paid media":          "paid-media",
    "paid-media":          "paid-media",
    "sales":               "sales",
    "finance":             "finance",
    "support":             "support",
    "academic":            "academic",
    "game development":    "game-development",
    "game-development":    "game-development",
    "spatial computing":   "spatial-computing",
    "spatial-computing":   "spatial-computing",
    "specialized":         "specialized",
}
# Matches `| DisplayName | <cells> |` rows. Excludes header / separator lines
# and backticked slug rows (those are handled by _CATEGORY_ROW_RE).
_DISPLAY_ROW_RE = re.compile(
    r"^\|\s*(?P<disp>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*?)\s*\|"
    r"(?P<rest>.+?)\|\s*$"
)


def check_count_claims(pack: Path) -> CheckResult:
    idx_path = pack / "agents" / "index.json"
    if not idx_path.exists():
        return CheckResult("count-claims", False,
                           "agents/index.json missing",
                           "Run: python3 agents/scripts/build_index.py")
    try:
        idx = json.loads(idx_path.read_text())
    except Exception as e:
        return CheckResult("count-claims", False,
                           f"index.json not valid JSON: {e}",
                           "Regenerate via build_index.py.")
    total = int(idx.get("counts", {}).get("total", 0))
    by_cat: dict = idx.get("counts", {}).get("by_category", {}) or {}
    known_cats = set(by_cat.keys())

    problems: list[str] = []
    for rel in _COUNT_CLAIM_FILES:
        fp = pack / rel
        if not fp.exists():
            continue
        try:
            text = fp.read_text(errors="replace")
        except Exception:
            continue
        for pat in _TOTAL_CLAIM_PATTERNS:
            for m in pat.finditer(text):
                n = int(m.group(1))
                if n != total:
                    line_no = text[:m.start()].count("\n") + 1
                    problems.append(
                        f"{rel}:{line_no} claims {n} agents "
                        f"(index.json has {total}): '{m.group(0).strip()}'"
                    )
        # Per-category rows, scoped to tables.
        # README / AGENTS / agents/README use the backticked-slug form.
        if rel in ("README.md", "AGENTS.md", "agents/README.md"):
            for ln, line in enumerate(text.splitlines(), 1):
                mr = _CATEGORY_ROW_RE.match(line)
                if not mr:
                    continue
                cat = mr.group("cat")
                if cat not in known_cats:
                    continue
                # Numbers in the rest of the row. Each | separates cells.
                cells = [c.strip() for c in mr.group("rest").split("|")]
                nums = [int(c) for c in cells if re.fullmatch(r"\d+", c)]
                if not nums:
                    continue
                expected = int(by_cat.get(cat, -1))
                if expected < 0:
                    continue
                if expected not in nums:
                    problems.append(
                        f"{rel}:{ln} category `{cat}` row has counts "
                        f"{nums}; index.json by_category[{cat}]={expected}"
                    )
        # GUIDE_EN.md uses a display-name table. Without this check the
        # guide silently drifted from index.json -- e.g. "Engineering |
        # 27" while the real pool already had 46.
        if rel == "GUIDE_EN.md":
            for ln, line in enumerate(text.splitlines(), 1):
                # Skip rows already covered by the slug form (first cell
                # begins with a backtick). _DISPLAY_ROW_RE's character
                # class refuses backticks, but guard explicitly for
                # clarity.
                if "`" in line.split("|", 2)[1:2][0] if "|" in line else "":
                    continue
                mr = _DISPLAY_ROW_RE.match(line)
                if not mr:
                    continue
                disp = mr.group("disp").strip().lower()
                slug = _DISPLAY_TO_SLUG.get(disp)
                if not slug or slug not in known_cats:
                    continue
                cells = [c.strip() for c in mr.group("rest").split("|")]
                nums = [int(c) for c in cells if re.fullmatch(r"\d+", c)]
                if not nums:
                    continue
                expected = int(by_cat.get(slug, -1))
                if expected < 0:
                    continue
                if expected not in nums:
                    problems.append(
                        f"{rel}:{ln} category '{mr.group('disp').strip()}' "
                        f"row has counts {nums}; "
                        f"index.json by_category[{slug}]={expected}"
                    )

    if problems:
        head = "; ".join(problems[:4])
        extra = "" if len(problems) <= 4 else f" (+{len(problems)-4} more)"
        return CheckResult(
            "count-claims", False,
            f"{len(problems)} stale count claim(s): {head}{extra}",
            "Update the advertised numbers to match agents/index.json "
            "(counts.total and counts.by_category), or regenerate the "
            "index via build_index.py if it is the one out of date.",
        )
    return CheckResult(
        "count-claims", True,
        f"all advertised counts agree with index.json (total={total})",
    )


def check_dirs(pack: Path) -> CheckResult:
    missing = [d for d in REQUIRED_DIRS if not (pack / d).is_dir()]
    if missing:
        return CheckResult(
            "required-dirs", False,
            f"missing directories: {missing}",
            "Pack is incomplete. Re-clone from upstream.",
        )
    return CheckResult("required-dirs", True,
                       f"all {len(REQUIRED_DIRS)} required directories present")


def check_scripts(pack: Path) -> CheckResult:
    missing: list[str] = []
    not_exec: list[str] = []
    for s in REQUIRED_SCRIPTS:
        p = pack / s
        if not p.exists():
            missing.append(s)
            continue
        if not (p.stat().st_mode & 0o111):
            not_exec.append(s)
    problems = []
    if missing:
        problems.append(f"missing: {missing}")
    if not_exec:
        problems.append(f"not executable: {not_exec}")
    if problems:
        return CheckResult(
            "required-scripts", False,
            "; ".join(problems),
            "Run: chmod +x agents/scripts/*.py agents/scripts/*.sh   then retry.",
        )
    return CheckResult("required-scripts", True,
                       f"{len(REQUIRED_SCRIPTS)} scripts present + executable")


def check_hooks(pack: Path) -> CheckResult:
    missing = [f for f in REQUIRED_HOOK_SCRIPTS if not (pack / f).exists()]
    if missing:
        return CheckResult(
            "hooks-subtree", False,
            f"missing hook files: {missing}",
            "Re-clone or restore from upstream.",
        )
    # Hook .sh files should be executable.
    not_exec = [
        f for f in REQUIRED_HOOK_SCRIPTS
        if f.endswith(".sh") and not (pack / f).stat().st_mode & 0o111
    ]
    if not_exec:
        return CheckResult(
            "hooks-subtree", False,
            f"hook scripts not executable: {not_exec}",
            "Run: chmod +x hooks/scripts/*.sh",
        )
    return CheckResult("hooks-subtree", True,
                       f"{len(REQUIRED_HOOK_SCRIPTS)} hook files present + executable")


def check_memory(pack: Path) -> CheckResult:
    missing = [f for f in REQUIRED_MEMORY_FILES if not (pack / f).exists()]
    if missing:
        return CheckResult(
            "memory-subtree", False,
            f"missing memory files: {missing}",
            "Re-clone or restore from upstream.",
        )
    rc = _run(["python3", str(pack / "memory" / "validate.py"),
               "--path", str(pack / "memory"), "--strict", "--quiet"], cwd=pack)
    if rc.returncode != 0:
        return CheckResult(
            "memory-subtree", False,
            f"shipped memory templates fail validation:\n{rc.stderr.strip()[:300]}",
            "Restore memory/ from upstream.",
        )
    return CheckResult("memory-subtree", True,
                       f"{len(REQUIRED_MEMORY_FILES)} memory files + templates validate")


def check_top_files(pack: Path) -> CheckResult:
    missing = [f for f in REQUIRED_TOP_FILES if not (pack / f).exists()]
    if missing:
        return CheckResult(
            "top-files", False,
            f"missing required files: {missing}",
            "Pack is incomplete; re-clone.",
        )
    return CheckResult("top-files", True,
                       f"{len(REQUIRED_TOP_FILES)} required top-level files present")


def check_tags_json(pack: Path) -> CheckResult:
    p = pack / "agents" / "tags.json"
    if not p.exists():
        return CheckResult("tags-json", False, "agents/tags.json missing",
                           "Re-clone.")
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return CheckResult("tags-json", False, f"tags.json not valid JSON: {e}",
                           "Restore from upstream.")
    tag_count = sum(1 for k in (data.get("tags") or {}).keys() if not k.startswith("_"))
    if tag_count < 50:
        return CheckResult("tags-json", False,
                           f"tags.json has only {tag_count} tags (expected >= 50)",
                           "Restore a complete tags.json from upstream.")
    return CheckResult("tags-json", True,
                       f"tags.json loads; {tag_count} curated tags available")


CHECKS = [
    check_python_version,
    check_version,
    check_changelog,
    check_top_files,
    check_dirs,
    check_scripts,
    check_hooks,
    check_memory,
    check_tags_json,
    check_agent_count,
    check_count_claims,
    check_index_fresh,
    check_py_guards_fresh,
    check_manifest,
    check_agent_safety,
    check_agent_freshness,
    check_lint,
    check_migrator_idempotent,
]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", type=Path,
                    help="Pack root. Default: directory containing this script's grandparent.")
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    ap.add_argument("--quiet", action="store_true", help="Only print failures.")
    ap.add_argument("--skip-slow", action="store_true",
                    help="Skip lint + migrator checks (faster sanity probe).")
    args = ap.parse_args(argv)

    pack = args.pack or Path(__file__).resolve().parent.parent.parent
    pack = pack.resolve()
    if not (pack / "VERSION").exists() and not (pack / "AGENTS.md").exists():
        print(f"check_pack_health: {pack} does not look like a pack root", file=sys.stderr)
        return 2

    results: list[CheckResult] = []
    for fn in CHECKS:
        if args.skip_slow and fn.__name__ in ("check_lint", "check_migrator_idempotent"):
            results.append(CheckResult(fn.__name__.replace("check_", ""),
                                        True, "(skipped)"))
            continue
        try:
            results.append(fn(pack))
        except Exception as e:
            results.append(CheckResult(
                fn.__name__.replace("check_", ""), False,
                f"check crashed: {e.__class__.__name__}: {e}",
                "Report this as a bug in the pack."))

    if args.json:
        print(json.dumps({
            "pack": str(pack),
            "results": [asdict(r) for r in results],
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r.passed),
                "failed": sum(1 for r in results if not r.passed),
            },
        }, indent=2))
    else:
        for r in results:
            if args.quiet and r.passed:
                continue
            icon = "✓" if r.passed else "✖"
            print(f"  {icon}  {r.name}: {r.message}")
            if not r.passed and r.fix:
                for fl in r.fix.splitlines():
                    print(f"       FIX: {fl}")
        failed = sum(1 for r in results if not r.passed)
        passed = len(results) - failed
        print("")
        print(f"  Summary: {passed}/{len(results)} passed, {failed} failure(s).")
        print("  Pack is healthy." if failed == 0 else
              "  Pack has problems -- fix before integrating into any project.")

    return 1 if any(not r.passed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
