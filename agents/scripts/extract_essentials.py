#!/usr/bin/env python3
"""
extract_essentials.py -- produce the thin 'essentials' version of a persona
agent. The result keeps the original frontmatter plus the body up to the
`## Deep Reference` marker (or, if absent, up to the first `## ` heading
past an 80-line budget).

Used by `scripts/convert.sh --thin` and by integration flows that want to
ship lean agent bodies into token-constrained IDE sessions.

Usage:
    extract_essentials.py <agent.md> [<agent.md> ...]      # print to stdout
    extract_essentials.py --out-dir DIR <agent.md> ...     # write *.essentials.md
    extract_essentials.py --inline <agent.md>              # show delta stats only
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
import sys
from dataclasses import dataclass
from pathlib import Path

# Marker that explicitly separates essentials from the deep-reference body.
DEEP_REF_MARKER = "## Deep Reference"

# Fallback: if no marker is present, cut at the first `## ` heading we
# encounter after this many non-blank body lines.
BUDGET_LINES = 80


@dataclass
class ExtractResult:
    source: Path
    original_body_lines: int
    essentials_body_lines: int
    cut_reason: str            # "marker" | "budget" | "full-body"
    essentials_text: str       # full content to write (includes frontmatter)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """Return (frontmatter_block_including_delimiters, body)."""
    if not raw.startswith("---\n"):
        return "", raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return "", raw
    return raw[: end + 5], raw[end + 5 :]


def _cut_body(body: str) -> tuple[str, str]:
    """Return (essentials_body, cut_reason)."""
    lines = body.splitlines(keepends=True)
    # 1. Explicit marker wins.
    for i, line in enumerate(lines):
        if line.strip() == DEEP_REF_MARKER:
            return "".join(lines[:i]).rstrip() + "\n", "marker"

    # 2. No marker: keep everything up to BUDGET_LINES of non-blank body,
    #    then cut at the next `## ` heading we encounter (greedy).
    non_blank = 0
    budget_hit_at = None
    for i, line in enumerate(lines):
        if line.strip():
            non_blank += 1
        if non_blank >= BUDGET_LINES:
            budget_hit_at = i
            break

    if budget_hit_at is None:
        return body, "full-body"

    # Seek the next `## ` heading past the budget point.
    for i in range(budget_hit_at, len(lines)):
        if lines[i].startswith("## "):
            return "".join(lines[:i]).rstrip() + "\n", "budget"

    # If no heading follows, keep the whole body -- nothing to cut cleanly.
    return body, "full-body"


def extract(path: Path) -> ExtractResult:
    raw = path.read_text()
    fm, body = _split_frontmatter(raw)
    essentials_body, reason = _cut_body(body)
    original_lines = sum(1 for l in body.splitlines() if l.strip())
    essentials_lines = sum(1 for l in essentials_body.splitlines() if l.strip())
    return ExtractResult(
        source=path,
        original_body_lines=original_lines,
        essentials_body_lines=essentials_lines,
        cut_reason=reason,
        essentials_text=fm + essentials_body,
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="Agent markdown files.")
    ap.add_argument("--out-dir", type=Path,
                    help="Write <slug>.essentials.md under this directory instead of printing to stdout.")
    ap.add_argument("--stats", action="store_true",
                    help="Print a one-line size summary per input file instead of contents.")
    args = ap.parse_args(argv)

    exit_code = 0
    for path in args.files:
        if not path.exists():
            print(f"ERROR: {path} does not exist", file=sys.stderr)
            exit_code = 1
            continue
        result = extract(path)

        if args.stats:
            saved = result.original_body_lines - result.essentials_body_lines
            print(
                f"  {result.original_body_lines:>4} -> {result.essentials_body_lines:<4} "
                f"(-{saved}, {result.cut_reason:<10}) {path.name}"
            )
            continue

        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            target = args.out_dir / f"{path.stem}.essentials.md"
            target.write_text(result.essentials_text)
            print(f"Wrote {target} ({result.essentials_body_lines} non-blank body lines, {result.cut_reason})")
        else:
            sys.stdout.write(result.essentials_text)

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
