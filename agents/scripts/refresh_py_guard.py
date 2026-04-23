#!/usr/bin/env python3
"""
refresh_py_guard.py -- re-inject the canonical Python-version guard
into every entry-point script in the pack.

The guard is the ~15-line snippet bracketed by
    # === PY-GUARD:BEGIN ===
    # === PY-GUARD:END ===
in `_py_guard_snippet.py`. This script copies that block into every
`.py` listed in `TARGETS`, inserting it immediately after the
shebang + optional module docstring + optional `from __future__`
imports, and BEFORE any other import.

Python 3.7+ accepts `from __future__ import annotations`, which must
be the first statement, so the guard runs after it. Users on 3.6 or
older see Python's own `SyntaxError: future feature annotations is
not defined` (Python 3.6 is EOL since 2021).

Idempotent: if an existing guard block is detected, its content is
replaced in-place.

Usage:
    python3 agents/scripts/refresh_py_guard.py          # refresh all
    python3 agents/scripts/refresh_py_guard.py --check  # CI: exit 1 if stale
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
import re
import sys
from pathlib import Path

BEGIN = "# === PY-GUARD:BEGIN ==="
END = "# === PY-GUARD:END ==="

HERE = Path(__file__).resolve().parent
SNIPPET_FILE = HERE / "_py_guard_snippet.py"
PACK_ROOT = HERE.parent.parent

TARGETS = [
    "agents/scripts/build_index.py",
    "agents/scripts/build_manifest.py",
    "agents/scripts/check_pack_health.py",
    "agents/scripts/deintegrate.py",
    "agents/scripts/detect_clones.py",
    "agents/scripts/detect_regression_commands.py",
    "agents/scripts/extract_essentials.py",
    "agents/scripts/integrate.py",
    "agents/scripts/lint_agents.py",
    "agents/scripts/merge_agents_md.py",
    "agents/scripts/migrate_schema.py",
    "agents/scripts/onboard.py",
    "agents/scripts/project_context.py",
    "agents/scripts/report_usage.py",
    "agents/scripts/run_regression.py",
    "agents/scripts/scan_agent_freshness.py",
    "agents/scripts/scan_agent_safety.py",
    "agents/scripts/scan_memory_leaks.py",
    "agents/scripts/scan_rules_conflicts.py",
    "agents/scripts/telemetry_webhook.py",
    "agents/scripts/smoke_test.py",
    "agents/scripts/upgrade.py",
    "agents/scripts/verify_integration.py",
    "memory/memory.py",
    "memory/validate.py",
]


def load_snippet() -> str:
    text = SNIPPET_FILE.read_text()
    m = re.search(
        re.escape(BEGIN) + r"(.*?)" + re.escape(END),
        text,
        flags=re.DOTALL,
    )
    if not m:
        print(f"ERROR: snippet markers not found in {SNIPPET_FILE}",
              file=sys.stderr)
        sys.exit(2)
    return (BEGIN + m.group(1) + END).rstrip() + "\n"


def _skip_header(lines: list[str]) -> int:
    """Return line index after shebang + coding + module docstring +
    any `from __future__ import ...` lines + any blank lines between.
    The guard is inserted at this index."""
    i = 0
    n = len(lines)
    if i < n and lines[i].startswith("#!"):
        i += 1
    if i < n and re.match(r"^#.*coding[:=]", lines[i]):
        i += 1
    while i < n and lines[i].strip() == "":
        i += 1

    # Module docstring
    if i < n:
        stripped = lines[i].lstrip()
        quote = None
        for q in ('"""', "'''"):
            if stripped.startswith(q):
                quote = q
                break
        if quote is not None:
            # Opens and closes on one line?
            if lines[i].count(quote) >= 2 and len(lines[i].strip()) > 5:
                i += 1
            else:
                i += 1
                while i < n and quote not in lines[i]:
                    i += 1
                if i < n:
                    i += 1
            while i < n and lines[i].strip() == "":
                i += 1

    # Any `from __future__ import ...` lines (possibly multiple).
    while i < n and re.match(r"^\s*from\s+__future__\s+import\s+", lines[i]):
        i += 1
    while i < n and lines[i].strip() == "":
        i += 1

    return i


def inject(source: str, guard: str) -> str:
    """Insert / replace the guard block in-place."""
    if BEGIN in source and END in source:
        # Use a callable replacement to avoid re.sub's backref expansion:
        # a literal `\n` in the string would otherwise be interpreted as a
        # newline escape, mangling the snippet. Callable replacements are
        # returned verbatim.
        return re.sub(
            re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?",
            lambda _m: guard,
            source,
            count=1,
            flags=re.DOTALL,
        )

    lines = source.splitlines(keepends=True)
    idx = _skip_header(lines)
    prefix = "".join(lines[:idx])
    suffix = "".join(lines[idx:])
    # Normalise spacing: one blank line before guard, one after.
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + guard + "\n" + suffix.lstrip("\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if any target is stale; do not modify.")
    args = ap.parse_args(argv)

    guard = load_snippet()
    stale: list[Path] = []
    for rel in TARGETS:
        p = PACK_ROOT / rel
        if not p.exists():
            print(f"  SKIP  {rel}  (missing)")
            continue
        current = p.read_text()
        updated = inject(current, guard)
        if updated != current:
            if args.check:
                stale.append(p)
                print(f"  STALE {rel}")
            else:
                p.write_text(updated)
                print(f"  UPDATE {rel}")
        else:
            print(f"  ok    {rel}")

    if args.check and stale:
        print(f"\nguard is stale in {len(stale)} file(s); "
              "run `python3 agents/scripts/refresh_py_guard.py`",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
