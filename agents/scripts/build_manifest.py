#!/usr/bin/env python3
"""
build_manifest.py -- emit MANIFEST.sha256 covering every file in the
pack that must ship unmodified to a project.

The manifest is the pack's supply-chain anchor. Any tool that cares
whether an agent file was tampered with (check_pack_health.py,
upgrade.py, verify_integration.py) consults this file. CI gates on
`--check` so no commit can ship with a stale manifest.

Output shape (sorted, deterministic, reproducible byte-for-byte):

    # harmonist MANIFEST v1 -- do not edit by hand.
    # Regenerate:  python3 agents/scripts/build_manifest.py
    # Verify:      python3 agents/scripts/build_manifest.py --check
    <sha256>  <relative/path>
    <sha256>  <relative/path>
    ...

Coverage (what's hashed):

  - AGENTS.md, integration-prompt.md, VERSION, CHANGELOG.md, README.md
  - agents/SCHEMA.md, agents/TAGS.md, agents/tags.json
  - every *.md under agents/<category>/
  - every agents/scripts/*.{py,sh} (excluding generated index.json)
  - every hooks/**/*.{json,sh}
  - every memory/*.{py,md}

Excluded (intentionally): agents/index.json (generated from agents),
MANIFEST.sha256 (can't hash itself), hooks/.state/, any __pycache__.

Usage:
    python3 agents/scripts/build_manifest.py                # write / refresh
    python3 agents/scripts/build_manifest.py --check        # CI: exit 1 if stale
    python3 agents/scripts/build_manifest.py --verify       # verify existing entries
    python3 agents/scripts/build_manifest.py --json         # emit JSON instead
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
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK_ROOT = HERE.parent.parent
MANIFEST_FILE = PACK_ROOT / "MANIFEST.sha256"

HEADER = [
    "# harmonist MANIFEST v1 -- do not edit by hand.",
    "# Regenerate:  python3 agents/scripts/build_manifest.py",
    "# Verify:      python3 agents/scripts/build_manifest.py --check",
]

# Files / globs to include. Order within this list doesn't matter -- we
# sort on emission. Glob patterns are evaluated relative to PACK_ROOT.
INCLUDE_PATTERNS = [
    "AGENTS.md",
    "integration-prompt.md",
    "VERSION",
    "CHANGELOG.md",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "GUIDE_EN.md",
    "agents/SCHEMA.md",
    "agents/STYLE.md",
    "agents/TAGS.md",
    "agents/tags.json",
    "agents/README.md",
    # Every agent in every category (recursive glob; sub-folders allowed).
    "agents/*/*.md",
    "agents/*/*/*.md",
    "agents/scripts/*.py",
    "agents/scripts/*.sh",
    # Pack-owned Cursor-rule templates (installed into .cursor/rules/
    # by upgrade.py; carry the canonical pack-owned marker).
    "agents/templates/rules/*.mdc",
    "hooks/hooks.json",
    "hooks/README.md",
    "hooks/scripts/*.sh",
    "memory/*.py",
    "memory/*.md",
    "memory/README.md",
    "memory/SCHEMA.md",
]

# Explicit excludes even if a glob above would pick them up. These are
# either generated (index.json), self-referential (MANIFEST), or
# private user-local artifacts (README.ru.md — kept by the maintainer
# for internal reference, not shipped in the public pack).
EXCLUDE_RELATIVE = {
    "agents/index.json",
    "MANIFEST.sha256",
    "README.ru.md",
}

# Directory names to prune everywhere.
EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".state",
    ".pytest_cache",
    ".mypy_cache",
    "tests",
    "integrations",
    "templates",
    "playbooks",
}


def _iter_files(root: Path) -> list[Path]:
    """Collect every file matching INCLUDE_PATTERNS minus excludes.

    Note: EXCLUDE_DIR_NAMES applies only to path components that come
    from recursive traversal; files picked up by an explicit glob
    pattern are trusted to be intentional (e.g. `agents/templates/
    rules/*.mdc`).
    """
    seen: set[Path] = set()
    for pat in INCLUDE_PATTERNS:
        # A glob with a literal "templates" / "integrations" segment is
        # explicit inclusion; skip the directory-blocklist check.
        explicit_override = any(
            seg in pat for seg in EXCLUDE_DIR_NAMES
        )
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if rel in EXCLUDE_RELATIVE:
                continue
            if not explicit_override:
                if any(seg in EXCLUDE_DIR_NAMES for seg in p.relative_to(root).parts):
                    continue
            seen.add(p)
    return sorted(seen)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_entries(root: Path) -> list[tuple[str, str]]:
    """Return sorted (sha, relpath) tuples."""
    entries: list[tuple[str, str]] = []
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        entries.append((_sha256_of(p), rel))
    entries.sort(key=lambda t: t[1])
    return entries


def format_manifest(entries: list[tuple[str, str]]) -> str:
    lines = list(HEADER)
    for sha, rel in entries:
        lines.append(f"{sha}  {rel}")
    return "\n".join(lines) + "\n"


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """Parse manifest text. Skips blank lines and comments."""
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        # sha256 (64 hex) + 2+ spaces + path
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, rel = parts[0], parts[1]
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            continue
        out.append((sha, rel))
    return out


def verify(root: Path, manifest_text: str) -> list[str]:
    """Return a list of discrepancies (empty list = clean)."""
    expected = {rel: sha for sha, rel in parse_manifest(manifest_text)}
    current = {rel: sha for sha, rel in build_entries(root)}

    problems: list[str] = []
    missing = sorted(set(expected) - set(current))
    extra = sorted(set(current) - set(expected))
    common = sorted(set(expected) & set(current))

    for rel in missing:
        problems.append(f"MISSING  {rel}")
    for rel in extra:
        problems.append(f"UNTRACKED {rel}  (sha={current[rel][:12]}…)")
    for rel in common:
        if expected[rel] != current[rel]:
            problems.append(
                f"CHANGED  {rel}\n"
                f"    expected {expected[rel]}\n"
                f"    actual   {current[rel]}"
            )
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if manifest is stale; do not write.")
    ap.add_argument("--verify", action="store_true",
                    help="Compare current pack contents to existing manifest; "
                         "exit 1 on any discrepancy.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", type=Path, default=PACK_ROOT)
    args = ap.parse_args(argv)

    root = args.root.resolve()

    if args.verify:
        if not MANIFEST_FILE.exists():
            print(f"error: {MANIFEST_FILE} missing; run without --verify to create",
                  file=sys.stderr)
            return 2
        problems = verify(root, MANIFEST_FILE.read_text())
        if args.json:
            print(json.dumps({"problems": problems,
                              "clean": not problems}, indent=2))
        else:
            if not problems:
                print(f"  manifest clean: {len(parse_manifest(MANIFEST_FILE.read_text()))} entries verified.")
            else:
                print(f"  {len(problems)} discrepancy(ies):")
                for p in problems:
                    for ln in p.splitlines():
                        print(f"    {ln}")
        return 0 if not problems else 1

    entries = build_entries(root)
    new_text = format_manifest(entries)

    if args.json:
        print(json.dumps(
            [{"sha256": sha, "path": rel} for sha, rel in entries],
            indent=2, sort_keys=False,
        ))
        return 0

    if args.check:
        old = MANIFEST_FILE.read_text() if MANIFEST_FILE.exists() else ""
        if old == new_text:
            print(f"  manifest is up to date ({len(entries)} entries).")
            return 0
        # Print a small diff summary.
        old_entries = dict((rel, sha) for sha, rel in parse_manifest(old))
        new_entries = dict((rel, sha) for sha, rel in entries)
        diffs = []
        for rel in sorted(set(old_entries) | set(new_entries)):
            o, n = old_entries.get(rel), new_entries.get(rel)
            if o is None:
                diffs.append(f"  + {rel}")
            elif n is None:
                diffs.append(f"  - {rel}")
            elif o != n:
                diffs.append(f"  ~ {rel}")
        print(f"  manifest is STALE ({len(diffs)} drift):", file=sys.stderr)
        for d in diffs[:40]:
            print(d, file=sys.stderr)
        if len(diffs) > 40:
            print(f"  ... and {len(diffs) - 40} more", file=sys.stderr)
        print("\n  Regenerate: python3 agents/scripts/build_manifest.py",
              file=sys.stderr)
        return 1

    MANIFEST_FILE.write_text(new_text)
    print(f"  wrote {MANIFEST_FILE.relative_to(root)} with {len(entries)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
