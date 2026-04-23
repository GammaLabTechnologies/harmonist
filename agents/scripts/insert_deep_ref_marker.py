#!/usr/bin/env python3
"""
insert_deep_ref_marker.py — add a `## Deep Reference` cut point to persona
agents that grew long enough that `extract_essentials.py` falls back to its
budget heuristic.

Heuristic for where to cut:
  1. If any H2 is a recognised "essentials boundary" (case-insensitive,
     emoji-stripped match against ESSENTIALS_BOUNDARIES), insert the
     marker right before that heading.
  2. Otherwise, find the first H2 that sits after at least MIN_LEAD_NONBLANK
     non-blank body lines AND has at least as many non-blank body lines
     below it as above -- that's the natural "reference material starts
     here" split -- and insert there.
  3. If nothing qualifies, print a warning and skip the file.

The marker is inserted as its own section heading: two blank lines above,
the line `## Deep Reference`, one blank line, then the original heading.
This keeps the downstream content intact and makes the cut point explicit
and authorial rather than heuristic.

Usage:
  insert_deep_ref_marker.py agents/<cat>/<slug>.md [<slug>.md ...]
  insert_deep_ref_marker.py --dry-run agents/<cat>/<slug>.md
  insert_deep_ref_marker.py --from-file targets.txt
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

MARKER_LINE = "## Deep Reference"

# Heading normalisation: strip leading emoji / non-ASCII clutter (including
# mojibake like "=Ë " where a multi-byte emoji was reinterpreted as Latin-1),
# then drop a leading "Your " (very common persona stylism).
_LEADING_NOISE = re.compile(r"^[^A-Za-z0-9]+")
_YOUR_PREFIX = re.compile(r"^Your\s+", re.IGNORECASE)

# Headings that mark the start of Deep Reference material. The first H2
# whose normalised text matches any of these wins.
ESSENTIALS_BOUNDARIES = {
    "technical deliverables",
    "audit deliverables",
    "deliverables",
    "deliverable template",
    "core capabilities",
    "brand strategy deliverables",
    "methodology",
    "test coverage analysis",
    "workflow process",
}

MIN_LEAD_NONBLANK = 40


def _normalise(h: str) -> str:
    # Strip every run of non-ASCII-alnum at the front (covers emoji,
    # mojibake like "=Ë " or ">à ", leading punctuation, stray spaces).
    prev = None
    while prev != h:
        prev = h
        h = _LEADING_NOISE.sub("", h).strip()
    h = _YOUR_PREFIX.sub("", h).strip()
    return h.lower()


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---\n"):
        return "", raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return "", raw
    return raw[: end + 5], raw[end + 5 :]


def _find_cut_index(body_lines: list[str]) -> tuple[int | None, str]:
    """Return (line_index_of_heading_to_precede, reason) or (None, reason)."""
    h2_positions: list[tuple[int, str, int]] = []  # (line_idx, heading_text, nonblank_before)
    nonblank = 0
    for i, line in enumerate(body_lines):
        if line.startswith("## ") and not line.startswith("## Deep Reference"):
            h2_positions.append((i, line[3:].strip(), nonblank))
        if line.strip():
            nonblank += 1
    total_nonblank = nonblank

    # Strategy 1: known essentials boundary.
    for idx, text, lead in h2_positions:
        if _normalise(text) in ESSENTIALS_BOUNDARIES and lead >= 10:
            return idx, f"boundary-match:{text!r}"

    # Strategy 2: first H2 after MIN_LEAD_NONBLANK where below-content
    # dominates above-content (i.e., the reference section is the bulk).
    for idx, text, lead in h2_positions:
        trail = total_nonblank - lead
        if lead >= MIN_LEAD_NONBLANK and trail >= lead:
            return idx, f"budget-split:{text!r}"

    return None, "no-candidate"


def insert_marker(path: Path, dry_run: bool = False) -> tuple[bool, str]:
    raw = path.read_text()
    if MARKER_LINE in raw.splitlines():
        return False, "already-has-marker"

    fm, body = _split_frontmatter(raw)
    if not fm:
        return False, "no-frontmatter"

    body_lines = body.splitlines(keepends=True)
    cut_idx, reason = _find_cut_index(body_lines)
    if cut_idx is None:
        return False, reason

    # Build the new body: lines before the cut, a blank line, marker,
    # a blank line, then the original heading and everything after.
    before = body_lines[:cut_idx]
    # Trim trailing blank lines from `before` so the spacing stays clean.
    while before and before[-1].strip() == "":
        before.pop()
    after = body_lines[cut_idx:]

    injected = (
        "".join(before).rstrip("\n")
        + "\n\n"
        + MARKER_LINE
        + "\n\n"
        + "".join(after)
    )
    new_raw = fm + injected

    if not dry_run:
        path.write_text(new_raw)
    return True, reason


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="Agent markdown files to update.")
    ap.add_argument("--from-file", type=Path, help="Read one path per line.")
    ap.add_argument("--dry-run", action="store_true", help="Report planned changes, write nothing.")
    args = ap.parse_args(argv)

    files: list[Path] = list(args.files)
    if args.from_file:
        for line in args.from_file.read_text().splitlines():
            line = line.strip()
            if line:
                files.append(Path(line))
    if not files:
        ap.error("no input files (pass paths or --from-file)")

    changed = 0
    skipped = 0
    for path in files:
        if not path.exists():
            print(f"  MISS  {path}: does not exist", file=sys.stderr)
            skipped += 1
            continue
        did_change, reason = insert_marker(path, dry_run=args.dry_run)
        if did_change:
            verb = "WOULD UPDATE" if args.dry_run else "UPDATED"
            print(f"  {verb:<12}  {path}  ({reason})")
            changed += 1
        else:
            print(f"  SKIP          {path}  ({reason})")
            skipped += 1

    print(f"\nSummary: {changed} changed, {skipped} skipped (of {len(files)}).")
    return 0 if skipped == 0 or changed > 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
