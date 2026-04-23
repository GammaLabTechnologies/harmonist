#!/usr/bin/env python3
"""
validate.py — enforce Memory Schema v1 on .cursor/memory/*.md files.

Called directly or by the Cursor enforcement hooks. Emits a non-zero
exit code plus human-readable errors to stderr when any memory file
violates the schema.

Usage:
    python3 validate.py                        # scan the memory/ dir next to this script
    python3 validate.py --path .cursor/memory  # scan a specific directory
    python3 validate.py --file session-handoff.md path/to/decisions.md   # scan specific files
    python3 validate.py --strict               # treat warnings as errors (used in CI)
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
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = "1"
KNOWN_SCHEMA_VERSIONS = {"1"}

# File ↔ kind contract
FILE_KIND = {
    "session-handoff.md": "state",
    "decisions.md": "decision",
    "patterns.md": "pattern",
}

VALID_KINDS = {"state", "decision", "pattern"}
VALID_STATUSES = {"in_progress", "done", "blocked", "rejected"}
VALID_AUTHORS = {"orchestrator", "subagent", "human"}

ENTRY_START = "<!-- memory-entry:start -->"
ENTRY_END = "<!-- memory-entry:end -->"

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CORRELATION_RE = re.compile(r"^\d+-\d+$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$")
SUMMARY_MAX = 160
BODY_MIN_CHARS = 20


@dataclass
class Entry:
    file: Path
    line_start: int
    line_end: int
    frontmatter: dict
    body: str

    @property
    def id(self) -> str | None:
        return self.frontmatter.get("id")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- frontmatter parsing
# Minimal YAML subset: flat key: value, inline lists [a, b], booleans, strings.
# Keeps the validator dependency-free.


def parse_frontmatter(raw: str, source: str, line_offset: int, report: Report) -> dict | None:
    fields: dict = {}
    for i, line in enumerate(raw.splitlines(), start=line_offset):
        if not line.strip():
            continue
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$", line)
        if not m:
            report.errors.append(f"{source}:{i}: frontmatter line does not parse: {line!r}")
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            fields[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        elif rest.lower() == "true":
            fields[key] = True
        elif rest.lower() == "false":
            fields[key] = False
        elif rest == "":
            fields[key] = ""
        else:
            fields[key] = rest.strip("'\"")
    return fields


# --------------------------------------------------------------------------- file scanning


def iter_entries(path: Path, report: Report) -> Iterable[Entry]:
    """Yield each <!-- memory-entry:start --> ... <!-- memory-entry:end --> block."""
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == ENTRY_START:
            start = i
            # find matching end
            j = i + 1
            while j < len(lines) and lines[j].strip() != ENTRY_END:
                if lines[j].strip() == ENTRY_START:
                    report.errors.append(
                        f"{path}:{j + 1}: nested memory-entry:start without preceding end"
                    )
                    break
                j += 1
            if j >= len(lines):
                report.errors.append(
                    f"{path}:{start + 1}: memory-entry:start with no matching end"
                )
                return
            block = lines[start + 1 : j]
            # Expect leading '---' / frontmatter / '---' / body
            # Skip leading blanks
            k = 0
            while k < len(block) and not block[k].strip():
                k += 1
            if k >= len(block) or block[k].strip() != "---":
                report.errors.append(
                    f"{path}:{start + 2 + k}: entry missing opening '---' for frontmatter"
                )
                i = j + 1
                continue
            fm_start = k + 1
            m = fm_start
            while m < len(block) and block[m].strip() != "---":
                m += 1
            if m >= len(block):
                report.errors.append(
                    f"{path}:{start + 2 + fm_start}: entry missing closing '---' for frontmatter"
                )
                i = j + 1
                continue
            fm_raw = "\n".join(block[fm_start:m])
            body = "\n".join(block[m + 1 :]).strip()
            fm = parse_frontmatter(fm_raw, str(path), start + 2 + fm_start, report)
            yield Entry(
                file=path,
                line_start=start + 1,
                line_end=j + 1,
                frontmatter=fm or {},
                body=body,
            )
            i = j + 1
        else:
            i += 1


# --------------------------------------------------------------------------- per-entry checks


def _check_required_fields(entry: Entry, report: Report) -> None:
    required = ["id", "correlation_id", "at", "kind", "status", "author", "summary"]
    for field_name in required:
        if field_name not in entry.frontmatter or entry.frontmatter[field_name] in ("", None):
            report.errors.append(
                f"{entry.file}:{entry.line_start}: entry missing required field '{field_name}'"
            )


def _check_enums(entry: Entry, report: Report) -> None:
    kind = entry.frontmatter.get("kind")
    status = entry.frontmatter.get("status")
    author = entry.frontmatter.get("author")
    if kind is not None and kind not in VALID_KINDS:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: kind={kind!r} not in {sorted(VALID_KINDS)}"
        )
    if status is not None and status not in VALID_STATUSES:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: status={status!r} not in {sorted(VALID_STATUSES)}"
        )
    if author is not None and author not in VALID_AUTHORS:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: author={author!r} not in {sorted(VALID_AUTHORS)}"
        )


def _check_schema_version(entry: Entry, report: Report) -> None:
    """Optional field. If present it must be a known version. When absent
    the entry is assumed to be SCHEMA_VERSION (backwards-compatible default)."""
    sv = entry.frontmatter.get("schema_version")
    if sv is None or sv == "":
        return
    if str(sv) not in KNOWN_SCHEMA_VERSIONS:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: schema_version={sv!r} is unknown "
            f"(known: {sorted(KNOWN_SCHEMA_VERSIONS)})."
        )


def _check_shapes(entry: Entry, report: Report) -> None:
    eid = entry.frontmatter.get("id", "")
    if eid and not ID_RE.match(str(eid)):
        report.errors.append(
            f"{entry.file}:{entry.line_start}: id={eid!r} must match [a-z0-9][a-z0-9-]*"
        )
    cid = entry.frontmatter.get("correlation_id", "")
    if cid and not CORRELATION_RE.match(str(cid)):
        report.errors.append(
            f"{entry.file}:{entry.line_start}: correlation_id={cid!r} must match <int>-<int>"
        )
    at = entry.frontmatter.get("at", "")
    if at and not ISO_RE.match(str(at)):
        report.errors.append(
            f"{entry.file}:{entry.line_start}: at={at!r} must be ISO 8601 UTC"
        )
    summary = entry.frontmatter.get("summary", "")
    if summary:
        if "\n" in summary:
            report.errors.append(
                f"{entry.file}:{entry.line_start}: summary must be single-line"
            )
        if len(summary) > SUMMARY_MAX:
            report.errors.append(
                f"{entry.file}:{entry.line_start}: summary length {len(summary)} > {SUMMARY_MAX}"
            )


def _check_body(entry: Entry, report: Report) -> None:
    compact = re.sub(r"\s+", "", entry.body)
    if len(compact) < BODY_MIN_CHARS:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: body has < {BODY_MIN_CHARS} non-whitespace chars"
        )


def _check_file_kind(entry: Entry, report: Report) -> None:
    expected = FILE_KIND.get(entry.file.name)
    # Support *.shared.md variants (e.g. decisions.shared.md → decision)
    if expected is None:
        for base, kind in FILE_KIND.items():
            stem = base.removesuffix(".md")
            if entry.file.name.startswith(stem + ".") or entry.file.name == base:
                expected = kind
                break
    if expected is None:
        report.warnings.append(
            f"{entry.file}: not a recognized memory filename; skipping kind check"
        )
        return
    actual = entry.frontmatter.get("kind")
    if actual is not None and actual != expected:
        report.errors.append(
            f"{entry.file}:{entry.line_start}: kind={actual!r}; this file requires kind={expected!r}"
        )


def _check_monotonic_at(entries: list[Entry], report: Report) -> None:
    prev: dt.datetime | None = None
    prev_line = 0
    for e in entries:
        at = e.frontmatter.get("at")
        if not at or not ISO_RE.match(str(at)):
            continue
        try:
            cur = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if prev is not None and cur < prev:
            report.errors.append(
                f"{e.file}:{e.line_start}: at={at} earlier than previous entry "
                f"at line {prev_line}; memory is append-only, entries must be monotonic"
            )
        prev, prev_line = cur, e.line_start


# --------------------------------------------------------------------------- main


def discover_files(base: Path) -> list[Path]:
    """Find all memory files under `base` (files whose stem starts with the
    canonical names, so *.shared.md variants are picked up too)."""
    if not base.exists():
        return []
    files: list[Path] = []
    for p in sorted(base.iterdir()):
        if not p.is_file() or p.suffix != ".md":
            continue
        for stem in FILE_KIND:
            bare = stem.removesuffix(".md")
            if p.name == stem or p.name.startswith(bare + "."):
                files.append(p)
                break
    return files


def validate(path_or_files: list[Path], strict: bool = False) -> Report:
    report = Report()
    all_entries: list[Entry] = []

    for path in path_or_files:
        if not path.exists():
            report.errors.append(f"{path}: does not exist")
            continue
        entries = list(iter_entries(path, report))
        for e in entries:
            _check_required_fields(e, report)
            _check_enums(e, report)
            _check_shapes(e, report)
            _check_file_kind(e, report)
            _check_body(e, report)
            _check_schema_version(e, report)
        _check_monotonic_at(entries, report)
        all_entries.extend(entries)

    # global uniqueness of `id`
    seen: dict[str, Entry] = {}
    for e in all_entries:
        eid = e.frontmatter.get("id")
        if not eid:
            continue
        if eid in seen:
            other = seen[eid]
            report.errors.append(
                f"{e.file}:{e.line_start}: duplicate id={eid!r} "
                f"(already used by {other.file}:{other.line_start})"
            )
        else:
            seen[eid] = e

    if strict and report.warnings:
        report.errors.extend(f"[strict] {w}" for w in report.warnings)
        report.warnings = []

    return report


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, help="Directory to validate (default: memory/ next to this script)")
    ap.add_argument("--file", type=Path, nargs="+", help="Specific files to validate")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    ap.add_argument("--quiet", action="store_true", help="Only emit errors on stderr, no summary")
    args = ap.parse_args(argv)

    if args.file:
        files = list(args.file)
    else:
        base = args.path or Path(__file__).resolve().parent
        files = discover_files(base)

    if not files:
        print("validate-memory: no memory files found", file=sys.stderr)
        return 0

    report = validate(files, strict=args.strict)

    for w in report.warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in report.errors:
        print(f"ERROR {e}", file=sys.stderr)

    if not args.quiet:
        print(
            f"Memory schema v{SCHEMA_VERSION}: "
            f"{len(files)} file(s), "
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s).",
            file=sys.stderr,
        )
        print("PASSED" if not report.errors else "FAILED", file=sys.stderr)

    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
