#!/usr/bin/env python3
"""
scan_memory_leaks.py -- audit a git repository for memory files that
were accidentally committed.

Memory lives in `.cursor/memory/` and typically contains project-
sensitive state (service names, deploy hosts, internal module layout,
sometimes raw secrets). It should stay local to each developer's
checkout. This script walks the current git tree + history and reports
every memory file that was ever tracked, with a ready-to-copy
`git rm --cached` / `git filter-repo` recipe to evict it.

Usage:
    python3 harmonist/agents/scripts/scan_memory_leaks.py
    python3 harmonist/agents/scripts/scan_memory_leaks.py --project /path/to/proj
    python3 harmonist/agents/scripts/scan_memory_leaks.py --json

Exit codes:
    0 = no leaks found
    1 = leaks found (either currently tracked OR present in history)
    2 = not a git repo / cannot run
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
import subprocess
import sys
from pathlib import Path

# Memory files that are INTENDED to be team-shared live under these names;
# the scanner never flags them.
SHARED_SUFFIXES = (".shared.md",)
EXPLICIT_OK = {"README.md", "SCHEMA.md"}
MEMORY_PREFIX = ".cursor/memory/"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def list_tracked(project: Path) -> list[str]:
    """Files CURRENTLY tracked (whether staged or committed)."""
    rc = _run(["git", "ls-files", "--", ".cursor/memory/"], project)
    if rc.returncode != 0:
        return []
    return [l for l in rc.stdout.splitlines() if l.strip()]


def list_ever_committed(project: Path) -> list[str]:
    """Files that ever existed under .cursor/memory/ in ANY commit."""
    # git log --diff-filter=A (added) OR just log --name-only restricted to path
    rc = _run(
        ["git", "log", "--all", "--pretty=format:", "--name-only", "--",
         ".cursor/memory/"],
        project,
    )
    if rc.returncode != 0:
        return []
    seen: set[str] = set()
    for line in rc.stdout.splitlines():
        line = line.strip()
        if line and line.startswith(MEMORY_PREFIX):
            seen.add(line)
    return sorted(seen)


def _is_shared(path: str) -> bool:
    basename = path.split("/")[-1]
    if basename in EXPLICIT_OK:
        return True
    return any(basename.endswith(s) for s in SHARED_SUFFIXES)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project root (must be a git repo). Default: current directory.")
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not (project / ".git").exists() and not (project / ".git").is_file():
        print(f"scan_memory_leaks: {project} is not a git repository", file=sys.stderr)
        return 2

    tracked = [p for p in list_tracked(project) if not _is_shared(p)]
    historical = [p for p in list_ever_committed(project) if not _is_shared(p)]
    historical_only = sorted(set(historical) - set(tracked))

    if args.json:
        print(json.dumps({
            "project": str(project),
            "tracked_now": tracked,
            "in_history_only": historical_only,
            "leaks": len(tracked) + len(historical_only),
        }, indent=2))
        return 1 if (tracked or historical_only) else 0

    # Human-readable.
    if not tracked and not historical_only:
        print(f"  ✓ no memory-file leaks found in {project}")
        return 0

    print(f"  Memory leaks in {project}:\n")
    if tracked:
        print(f"  Currently tracked ({len(tracked)}):")
        for t in tracked:
            print(f"    - {t}")
        print("\n  Evict them from git without deleting local copies:\n")
        print(f"    git rm --cached {' '.join(tracked)}")
        print(f"    git commit -m 'chore(memory): untrack session-handoff / decisions / patterns'")
        print()

    if historical_only:
        print(f"  Present in history only (not tracked now) ({len(historical_only)}):")
        for h in historical_only:
            print(f"    - {h}")
        print("\n  These files still live in past commits. To purge them from")
        print("  history (rewrites refs; coordinate with team):\n")
        print("    git filter-repo \\")
        for h in historical_only:
            print(f"      --path {h} --invert-paths \\")
        print("    # (install git-filter-repo first: pipx install git-filter-repo)")
        print()

    print("  Make sure .gitignore carries the memory-privacy block:")
    print("    python3 harmonist/agents/scripts/upgrade.py --apply")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
