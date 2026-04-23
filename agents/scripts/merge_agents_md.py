#!/usr/bin/env python3
"""
merge_agents_md.py -- splice pack-owned sections from the pack's AGENTS.md
into a project's AGENTS.md without touching project-owned content.

The pack's AGENTS.md has marker pairs:

    <!-- pack-owned:begin id="precedence" -->
    ## Precedence
    ...
    <!-- pack-owned:end -->

For every id present in BOTH files, this script replaces the project's
entire marker block (begin line -> end line, inclusive) with the pack's
current version. Everything outside marker blocks -- project identity,
Platform Stack, Modules, Invariants, Resilience, and any prose the user
added -- stays verbatim.

If the project file has NO pack-owned markers at all, the script refuses
to merge and asks the user to run the one-time bootstrap migration
instead. That protects projects that integrated before markers existed.

Usage:
    merge_agents_md.py --pack <pack-root> --project <project-root>
                       [--apply]  [--diff]  [--json]

Default is a dry-run preview.
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
import difflib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

BEGIN_RE = re.compile(r'^<!--\s*pack-owned:begin\s+id="([a-z0-9-]+)"\s*-->\s*$')
END_RE = re.compile(r'^<!--\s*pack-owned:end\s*-->\s*$')


@dataclass
class Block:
    id: str
    begin: int
    end: int      # inclusive
    lines: list[str]


@dataclass
class ParsedFile:
    path: Path
    lines: list[str]
    blocks: dict[str, Block] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse(path: Path) -> ParsedFile:
    text = path.read_text().splitlines()
    pf = ParsedFile(path=path, lines=text)
    i = 0
    while i < len(text):
        m = BEGIN_RE.match(text[i])
        if not m:
            i += 1
            continue
        bid = m.group(1)
        start = i
        j = i + 1
        while j < len(text) and not END_RE.match(text[j]):
            # Nested begin?  Treat as a structural error.
            if BEGIN_RE.match(text[j]):
                pf.errors.append(
                    f"{path}:{j+1}: nested pack-owned:begin inside '{bid}' block"
                )
                break
            j += 1
        if j >= len(text):
            pf.errors.append(f"{path}:{start+1}: unterminated pack-owned:begin id={bid!r}")
            break
        if bid in pf.blocks:
            pf.errors.append(
                f"{path}:{start+1}: duplicate pack-owned block id={bid!r} "
                f"(first at line {pf.blocks[bid].begin+1})"
            )
        else:
            pf.blocks[bid] = Block(id=bid, begin=start, end=j, lines=text[start : j + 1])
        i = j + 1
    return pf


@dataclass
class MergeReport:
    pack_path: Path
    project_path: Path
    replaced: list[str] = field(default_factory=list)
    inserted: list[str] = field(default_factory=list)
    orphan_project: list[str] = field(default_factory=list)  # in project but not pack
    unchanged: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    output: str = ""


def merge(pack_file: Path, project_file: Path) -> MergeReport:
    pack = parse(pack_file)
    proj = parse(project_file)
    report = MergeReport(pack_path=pack_file, project_path=project_file)

    if pack.errors or proj.errors:
        report.errors.extend(pack.errors)
        report.errors.extend(proj.errors)
        return report

    if not pack.blocks:
        report.errors.append(f"{pack_file}: no pack-owned markers found; nothing to merge.")
        return report

    if not proj.blocks:
        report.errors.append(
            f"{project_file}: no pack-owned markers found. This project was "
            f"integrated before markers existed. Run the bootstrap migration "
            f"(re-copy AGENTS.md from the pack while preserving your "
            f"project-owned sections) once, then merges will work."
        )
        return report

    # Walk project file line-by-line, substituting pack-owned blocks.
    out: list[str] = []
    i = 0
    while i < len(proj.lines):
        line = proj.lines[i]
        m = BEGIN_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        bid = m.group(1)
        # Find the end of this block in the project.
        block = proj.blocks.get(bid)
        if block is None:
            # Shouldn't happen -- parse() already populated it. Be safe.
            out.append(line)
            i += 1
            continue
        if bid in pack.blocks:
            pack_block = pack.blocks[bid]
            # Replace the project's entire block (begin..end) with pack's.
            out.extend(pack_block.lines)
            if block.lines != pack_block.lines:
                report.replaced.append(bid)
            else:
                report.unchanged.append(bid)
        else:
            # Project has a block id the pack no longer knows about.
            # Preserve it verbatim but flag as orphan for review.
            out.extend(block.lines)
            report.orphan_project.append(bid)
        i = block.end + 1

    # New pack blocks the project doesn't have yet -> append at the end,
    # preceded by a visible separator so the user can review placement.
    missing_in_project = [bid for bid in pack.blocks if bid not in proj.blocks]
    if missing_in_project:
        if out and out[-1].strip() != "":
            out.append("")
        out.append("<!-- pack-owned additions (please review placement) -->")
        for bid in missing_in_project:
            out.append("")
            out.extend(pack.blocks[bid].lines)
            report.inserted.append(bid)

    report.output = "\n".join(out).rstrip() + "\n"
    return report


# ---------------------------------------------------------------------------


def _render_diff(before: str, after: str, label_before: str, label_after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=label_before,
        tofile=label_after,
        n=3,
    ))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", type=Path, required=True,
                    help="Pack root directory (contains AGENTS.md + VERSION).")
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project root. Default: current directory.")
    ap.add_argument("--apply", action="store_true", help="Actually write the merged file.")
    ap.add_argument("--diff", action="store_true", help="Print a unified diff of the change.")
    ap.add_argument("--json", action="store_true", help="Machine-readable summary output.")
    args = ap.parse_args(argv)

    pack_file = (args.pack / "AGENTS.md").resolve()
    proj_file = (args.project / "AGENTS.md").resolve()
    if not pack_file.exists():
        print(f"merge_agents_md: pack AGENTS.md missing at {pack_file}", file=sys.stderr)
        return 2
    if not proj_file.exists():
        print(f"merge_agents_md: project AGENTS.md missing at {proj_file}", file=sys.stderr)
        return 2

    report = merge(pack_file, proj_file)

    if report.errors and not args.json:
        for e in report.errors:
            print(f"ERROR {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "pack_path": str(report.pack_path),
            "project_path": str(report.project_path),
            "replaced": report.replaced,
            "inserted": report.inserted,
            "orphan_in_project": report.orphan_project,
            "unchanged": report.unchanged,
            "errors": report.errors,
            "changed": bool(report.replaced or report.inserted),
        }, indent=2))
        return 2 if report.errors else 0

    # Human-readable.
    before_text = proj_file.read_text()
    changed = report.output != before_text
    print(f"  pack:    {pack_file}")
    print(f"  project: {proj_file}")
    print(f"  replaced:  {report.replaced or '(none)'}")
    print(f"  inserted:  {report.inserted or '(none)'}")
    print(f"  orphan:    {report.orphan_project or '(none)'}")
    print(f"  unchanged: {len(report.unchanged)} block(s)")
    if args.diff and changed:
        print("")
        print(_render_diff(before_text, report.output,
                           str(proj_file), str(proj_file) + " (merged)"))
    if args.apply:
        if changed:
            proj_file.write_text(report.output)
            print(f"\n  applied -- {proj_file} updated")
            return 1
        else:
            print("\n  already up to date; nothing written")
            return 0
    if changed:
        print("\n  dry-run: re-run with --apply to write the merged file.")
        return 0
    print("\n  project AGENTS.md is already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
