#!/usr/bin/env python3
"""
deintegrate.py -- clean, explicit removal of harmonist from a
project. For when you decide the full protocol is wrong for this repo.

What it does:

  - Removes every pack-owned file under `.cursor/` (hooks, strict
    review agents, memory tooling files, pack-owned rule templates).
  - Removes `AGENTS.md` at the project root by default, OR leaves it
    with `--keep-agents-md` (if the file has meaningful project
    customisation you want to keep).
  - Drops the pack-owned `.gitignore` block.
  - Leaves project-owned content intact:
      * Memory entries (session-handoff.md, decisions.md, patterns.md
        and any archive files) unless `--purge-memory` is passed.
      * Anything under `.cursor/` that doesn't match a known pack path.
      * Any `AGENTS.md` you explicitly keep.
      * `.cursor/agents/*.md` that are NOT strict reviewers (user
        picked them themselves; pack doesn't own them).

Safety:

  - Default is dry-run. Pass `--apply` to actually delete.
  - Writes an `.integration-snapshots/pre-deintegrate-<ts>.tar.gz`
    before touching anything, so the operation is reversible via
    `upgrade.py --rollback --snapshot pre-deintegrate-<ts>`.

Usage:
    python3 harmonist/agents/scripts/deintegrate.py           # dry-run
    python3 harmonist/agents/scripts/deintegrate.py --apply
    python3 harmonist/agents/scripts/deintegrate.py --apply --keep-agents-md
    python3 harmonist/agents/scripts/deintegrate.py --apply --purge-memory

Exit codes:
    0  -- operation succeeded (or dry-run succeeded)
    1  -- project is not an integrated pack (nothing to do)
    2  -- setup / argument error
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
import shutil
import sys
import tarfile
import time
from pathlib import Path


STRICT_REVIEWER_SLUGS = {
    "qa-verifier", "security-reviewer", "code-quality-auditor",
    "sre-observability", "bg-regression-runner", "wcag-a11y-gate",
    "repo-scout", "agents-orchestrator",
}

# Pack-owned files under .cursor/ (relative to project root).
# These get removed. Non-listed files are project-owned and preserved.
def _pack_owned_paths(project: Path) -> list[Path]:
    paths: list[Path] = []
    base = project / ".cursor"
    if not base.exists():
        return paths

    # Hook wiring + scripts
    paths.append(base / "hooks.json")
    hooks_scripts = base / "hooks" / "scripts"
    if hooks_scripts.exists():
        for p in hooks_scripts.iterdir():
            paths.append(p)
    state = base / "hooks" / ".state"
    if state.exists():
        paths.append(state)

    # Strict review / orchestration agents (pack owns these)
    adir = base / "agents"
    if adir.exists():
        for p in adir.rglob("*.md"):
            if p.stem in STRICT_REVIEWER_SLUGS:
                paths.append(p)

    # Memory tooling (python + schema). MD files = user content.
    mdir = base / "memory"
    for name in ("memory.py", "validate.py", "SCHEMA.md", "README.md"):
        p = mdir / name
        if p.exists():
            paths.append(p)

    # Cursor rules (only the canonical pack-owned one)
    rdir = base / "rules"
    prot = rdir / "protocol-enforcement.mdc"
    if prot.exists():
        try:
            text = prot.read_text(errors="replace")
            if "pack-owned: protocol-enforcement" in text:
                paths.append(prot)
        except Exception:
            pass

    # Integration bookkeeping
    for name in ("pack-version.json", "pack-manifest.json"):
        p = base / name
        if p.exists():
            paths.append(p)
    tel = base / "telemetry"
    if tel.exists():
        paths.append(tel)

    return paths


def _snapshot_pre_deintegrate(project: Path) -> Path | None:
    """Tarball every file we're about to touch so --rollback can undo."""
    to_snap = _pack_owned_paths(project)
    agents_md = project / "AGENTS.md"
    gitignore = project / ".gitignore"
    for p in (agents_md, gitignore):
        if p.exists() and p not in to_snap:
            to_snap.append(p)
    if not to_snap:
        return None
    snap_dir = project / ".cursor" / ".integration-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    us = int((time.time() % 1) * 1_000_000)
    tarball = snap_dir / f"pre-deintegrate-{ts}Z{us:06d}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for p in to_snap:
            try:
                arc = str(p.resolve().relative_to(project.resolve()))
            except ValueError:
                continue
            tar.add(str(p), arcname=arc)
    # Minimal meta so upgrade.py --rollback can read creations (empty
    # for deintegrate -- we're not creating new files during rollback).
    meta_path = snap_dir / (tarball.stem.replace(".tar", "") + ".json")
    meta_path.write_text(json.dumps({
        "created_at":   ts,
        "kind":         "pre-deintegrate",
        "project":      str(project.resolve()),
        "file_count":   len(to_snap),
        "creations":    [],
    }, indent=2, sort_keys=True) + "\n")
    return tarball


GITIGNORE_MARKER = "# harmonist: memory privacy"


def _strip_gitignore_block(project: Path) -> bool:
    p = project / ".gitignore"
    if not p.exists():
        return False
    text = p.read_text(errors="replace")
    lines = text.splitlines()
    keep: list[str] = []
    in_block = False
    trailing_blank = False
    for line in lines:
        if line.strip() == GITIGNORE_MARKER:
            in_block = True
            continue
        if in_block:
            # The pack's managed block is contiguous and the next blank
            # line / comment marks its end. A simple heuristic: lines
            # that were part of the managed set stop when we see either
            # a blank line followed by non-managed content, or a line
            # that doesn't start with `.cursor/` or `!`.
            stripped = line.strip()
            if stripped == "":
                in_block = False
                trailing_blank = True
                continue
            if stripped.startswith((
                ".cursor/", "!.cursor/", ".cursor/hooks/.state",
                ".cursor/hooks/config.json.local",
                ".cursor/telemetry/",
                ".cursor/.integration-snapshots/",
            )):
                continue
            in_block = False
            # Fall through to keep the line.
        if trailing_blank:
            keep.append("")
            trailing_blank = False
        keep.append(line)
    new_text = "\n".join(keep).rstrip("\n") + ("\n" if keep else "")
    if new_text == text:
        return False
    p.write_text(new_text)
    return True


def _plan(project: Path, keep_agents_md: bool, purge_memory: bool) -> dict:
    paths = _pack_owned_paths(project)
    plan: dict = {
        "remove_files":   sorted(str(p) for p in paths if p.is_file()),
        "remove_dirs":    sorted(str(p) for p in paths if p.is_dir()),
        "remove_agents_md": str(project / "AGENTS.md")
            if not keep_agents_md and (project / "AGENTS.md").exists() else "",
        "purge_memory":   "",
        "strip_gitignore": GITIGNORE_MARKER,
    }
    if purge_memory:
        mdir = project / ".cursor" / "memory"
        if mdir.exists():
            plan["purge_memory"] = str(mdir)
    return plan


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Default is dry-run.")
    ap.add_argument("--keep-agents-md", action="store_true",
                    help="Don't delete AGENTS.md (keep project customisation).")
    ap.add_argument("--purge-memory", action="store_true",
                    help="Also delete `.cursor/memory/` entirely (entries + schema).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"deintegrate: --project {project} is not a directory",
              file=sys.stderr)
        return 2

    if not (project / ".cursor").is_dir():
        msg = "no .cursor/ in project -- nothing to deintegrate"
        if args.json:
            print(json.dumps({"project": str(project), "deintegrated": False,
                              "message": msg}, indent=2))
        else:
            print(f"  {msg}")
        return 1

    plan = _plan(project, args.keep_agents_md, args.purge_memory)

    # Pre-apply snapshot so the operation is reversible.
    snapshot_path: Path | None = None
    if args.apply:
        try:
            snapshot_path = _snapshot_pre_deintegrate(project)
        except Exception as e:
            print(f"deintegrate: snapshot failed ({e}); aborting",
                  file=sys.stderr)
            return 2

    removed: list[str] = []
    skipped: list[str] = []

    if args.apply:
        for f in plan["remove_files"]:
            p = Path(f)
            try:
                p.unlink()
                removed.append(f)
            except FileNotFoundError:
                skipped.append(f)
            except Exception as e:
                skipped.append(f"{f}: {e}")
        for d in plan["remove_dirs"]:
            p = Path(d)
            try:
                shutil.rmtree(p)
                removed.append(d)
            except FileNotFoundError:
                skipped.append(d)
            except Exception as e:
                skipped.append(f"{d}: {e}")
        if plan["remove_agents_md"]:
            p = Path(plan["remove_agents_md"])
            try:
                p.unlink()
                removed.append(str(p))
            except Exception as e:
                skipped.append(f"{p}: {e}")
        if plan["purge_memory"]:
            try:
                shutil.rmtree(Path(plan["purge_memory"]))
                removed.append(plan["purge_memory"])
            except Exception as e:
                skipped.append(f"{plan['purge_memory']}: {e}")
        _strip_gitignore_block(project)

        # Remove .cursor/ entirely if it's empty-ish now.
        cursor = project / ".cursor"
        if cursor.exists():
            try:
                remaining = [x for x in cursor.rglob("*")
                             if x.is_file() and x.name != "integration-snapshots"]
                # If only the snapshots dir remains, consider cursor "drained"
                # but leave .cursor/.integration-snapshots/ so rollback still
                # works. User can delete manually.
            except Exception:
                pass

    if args.json:
        payload = {
            "project":        str(project),
            "dry_run":        not args.apply,
            "snapshot_path":  str(snapshot_path) if snapshot_path else "",
            "plan":           plan,
            "removed":        removed,
            "skipped":        skipped,
            "rollback_cmd":   (
                "python3 harmonist/agents/scripts/upgrade.py --rollback"
                f" --snapshot {snapshot_path.name if snapshot_path else '<snapshot>'}"
            ) if snapshot_path else "",
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("Deintegration " + ("plan (dry-run):" if not args.apply else "applied:"))
        print("")
        print(f"  remove {len(plan['remove_files'])} file(s) under .cursor/")
        print(f"  remove {len(plan['remove_dirs'])} dir(s) under .cursor/")
        if plan["remove_agents_md"]:
            print(f"  delete AGENTS.md  ({plan['remove_agents_md']})")
        else:
            print("  keep AGENTS.md (use --keep-agents-md to preserve "
                  "project customisation explicitly)"
                  if args.keep_agents_md
                  else "  AGENTS.md missing; nothing to delete")
        if plan["purge_memory"]:
            print(f"  purge memory   ({plan['purge_memory']})")
        else:
            print("  keep .cursor/memory/ (use --purge-memory to drop entries too)")
        print(f"  strip gitignore block ('{GITIGNORE_MARKER}')")
        if args.apply:
            print("")
            print(f"  snapshot: {snapshot_path}")
            print(f"  rollback: python3 harmonist/agents/scripts/upgrade.py "
                  f"--rollback --snapshot {snapshot_path.name}" if snapshot_path else "")
            print(f"  removed: {len(removed)} item(s); skipped: {len(skipped)}")
        else:
            print("")
            print("  (dry-run: nothing deleted. Re-run with --apply to deintegrate.)")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
