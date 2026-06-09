#!/usr/bin/env python3
"""
migrations.py -- memory-entry schema migration registry.

Mirrors agents/scripts/migrate_schema.py in purpose: if we ever bump
Memory Schema v1 to v2 (or beyond), teams with old memory files need a
deterministic upgrade path that does not require hand-editing. This
module holds the migration functions and a CLI that walks every entry
forward to the current schema.

Current state: only v1 exists. `MIGRATIONS` is intentionally empty and
serves as the anchor point for future additions. When v2 ships, the
workflow is:

    1. Implement `_upgrade_v1_to_v2(entry) -> entry` in this file.
    2. Register it: `MIGRATIONS[("1", "2")] = _upgrade_v1_to_v2`.
    3. Bump `CURRENT_MEMORY_SCHEMA_VERSION` to "2" and add "2" to
       `KNOWN_MEMORY_SCHEMA_VERSIONS` in validate.py.
    4. Add a test scenario to memory/tests/run-memory-tests.sh.
    5. Run `python3 memory/migrations.py --apply` on the pack itself
       and commit the diff.

The machinery is dependency-free and idempotent: running the CLI on a
fully-migrated file is a no-op. Running on a mixed file walks each
entry forward until it reaches current.

Usage:
    python3 migrations.py                    # dry-run, show what would change
    python3 migrations.py --apply            # rewrite files in place
    python3 migrations.py --path <dir>       # scan specific dir
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
# Force UTF-8 on stdio so status glyphs (checkmarks, arrows) print on legacy
# Windows code pages (cp1252) instead of raising UnicodeEncodeError. Reached
# only on Python 3.9+ (older interpreters exit above); a stream without
# .reconfigure (e.g. a captured StringIO) simply keeps its current encoding.
try:
    _asp_sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    _asp_sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
# === PY-GUARD:END ===

import argparse
import sys
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from validate import (  # noqa: E402
    discover_files,
    iter_entries,
    Report,
    SCHEMA_VERSION as CURRENT_MEMORY_SCHEMA_VERSION,
    KNOWN_SCHEMA_VERSIONS as KNOWN_MEMORY_SCHEMA_VERSIONS,
)


# A migration takes a parsed entry dict (frontmatter + body) and returns
# a dict of the same shape upgraded one version step. Migrations MUST
# be:
#   - deterministic: same input -> same output, byte-for-byte
#   - idempotent when combined with validation: the output is valid at
#     the target version and never produces warnings/errors
#   - non-destructive: any field it cannot map forward is preserved
#     under a namespaced key (e.g. `_legacy.v1.foo`) rather than dropped
Migration = Callable[[dict], dict]

MIGRATIONS: dict[tuple[str, str], Migration] = {
    # ("1", "2"): _upgrade_v1_to_v2,
}


def _plan_chain(start: str) -> list[tuple[str, str]]:
    """Return the ordered list of (from, to) migrations needed to reach
    CURRENT_MEMORY_SCHEMA_VERSION starting from `start`. Empty list
    when `start` IS current or when no path exists."""
    if start == CURRENT_MEMORY_SCHEMA_VERSION:
        return []
    chain: list[tuple[str, str]] = []
    cur = start
    guard = 0
    while cur != CURRENT_MEMORY_SCHEMA_VERSION and guard < 50:
        nxt = None
        for (frm, to) in MIGRATIONS:
            if frm == cur:
                nxt = to
                chain.append((frm, to))
                cur = to
                break
        if nxt is None:
            return []  # no path from `cur` onward
        guard += 1
    return chain


def migrate_entry(frontmatter: dict) -> tuple[dict, list[str]]:
    """Walk a single entry forward to CURRENT_MEMORY_SCHEMA_VERSION.
    Returns (migrated_frontmatter, applied_steps).
    `applied_steps` is a list of "vA -> vB" labels for logging."""
    version = str(frontmatter.get("schema_version", CURRENT_MEMORY_SCHEMA_VERSION))
    if version == CURRENT_MEMORY_SCHEMA_VERSION:
        return frontmatter, []
    if version not in KNOWN_MEMORY_SCHEMA_VERSIONS:
        raise ValueError(
            f"unknown schema_version={version!r}; "
            f"known: {sorted(KNOWN_MEMORY_SCHEMA_VERSIONS)}. "
            "Add it to KNOWN_SCHEMA_VERSIONS in validate.py before "
            "running migrations."
        )
    chain = _plan_chain(version)
    if not chain:
        raise ValueError(
            f"no migration path from v{version} to "
            f"v{CURRENT_MEMORY_SCHEMA_VERSION}. "
            "Register the missing step(s) in MIGRATIONS."
        )
    applied: list[str] = []
    cur_fm = dict(frontmatter)
    for frm, to in chain:
        cur_fm = MIGRATIONS[(frm, to)](cur_fm)
        cur_fm["schema_version"] = to
        applied.append(f"v{frm}->v{to}")
    return cur_fm, applied


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=SCRIPT_DIR,
                    help="Directory to scan (default: memory/ next to this script)")
    ap.add_argument("--apply", action="store_true",
                    help="Rewrite files in place; otherwise dry-run")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    files = discover_files(args.path)
    if not files:
        print(f"no memory files found under {args.path}", file=sys.stderr)
        return 0

    if not MIGRATIONS:
        print(
            f"migrations.py: no migrations registered; "
            f"every entry expected at schema v{CURRENT_MEMORY_SCHEMA_VERSION}."
        )
        # Still validate that every entry is at current -- a mismatch
        # here means somebody hand-edited a stale schema_version.
        bad = 0
        for f in files:
            report = Report()
            for e in iter_entries(f, report):
                sv = str(e.frontmatter.get("schema_version", CURRENT_MEMORY_SCHEMA_VERSION))
                if sv != CURRENT_MEMORY_SCHEMA_VERSION:
                    print(
                        f"  WARN  {f}:{e.line_start}: "
                        f"schema_version={sv!r} but no migration registered.",
                        file=sys.stderr,
                    )
                    bad += 1
        return 1 if bad else 0

    touched = 0
    for f in files:
        report = Report()
        entries = list(iter_entries(f, report))
        plan = []
        for e in entries:
            sv = str(e.frontmatter.get("schema_version", CURRENT_MEMORY_SCHEMA_VERSION))
            if sv == CURRENT_MEMORY_SCHEMA_VERSION:
                continue
            _, steps = migrate_entry(e.frontmatter)
            if steps:
                plan.append((e.line_start, sv, steps))
        if not plan:
            continue
        print(f"{f}: {len(plan)} entry(ies) need migration")
        if args.verbose:
            for line, sv, steps in plan:
                print(f"  line {line}: v{sv} -> {' -> '.join(steps)}")
        if args.apply:
            # When actual migrations land, this branch needs to
            # rewrite the file in place entry-by-entry. For now it's
            # a placeholder that refuses to corrupt files when no
            # migration body exists.
            print(
                "  (apply is not wired yet because MIGRATIONS is empty; "
                "add the functions first, then this branch rewrites files.)",
                file=sys.stderr,
            )
        touched += 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
