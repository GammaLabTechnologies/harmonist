#!/usr/bin/env python3
"""
upgrade.py -- roll a project forward to a newer pack version.

Run from a target project's root:

    python3 harmonist/agents/scripts/upgrade.py           # dry-run
    python3 harmonist/agents/scripts/upgrade.py --diff    # show file diffs
    python3 harmonist/agents/scripts/upgrade.py --apply   # actually copy

The script keeps a clean split between files the pack OWNS (and is
allowed to overwrite) and files the PROJECT owns (must never be
touched by upgrades). Pack-owned files are refreshed from the pack
source; project-owned files stay put. After a successful `--apply`
the project's `.cursor/pack-version.json` is updated.

Exit codes:
    0   dry-run succeeded / apply succeeded with nothing to do
    1   apply succeeded with changes (still OK)
    2   user-facing error (missing AGENTS.md, version regression)
    3   cannot locate pack source
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
# === PY-GUARD:END ===

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# What the pack OWNS: these files are always identical to the pack source.
#
# The strict-agent set is derived from agents/index.json via strict_slugs.py
# (shared with verify_integration / onboard / install_extras / deintegrate,
# so the lists can no longer diverge).
# NOTE: bg-regression-runner is intentionally EXCLUDED -- each project
# customises the test/lint/build commands in its body. Upgrading the pack
# must not wipe those; it is seeded separately below.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from strict_slugs import INSTALLED_BY_UPGRADE_RELPATHS as PACK_OWNED_STRICT_SLUGS  # noqa: E402
from merge_agents_md import (  # noqa: E402
    merge as merge_agents_md_fn,
    pack_dir_relname,
    pack_template_path,
)


# ---------------------------------------------------------------------------
# Cross-platform hook interpreter
#
# The pack's hooks.json template launches the Python runner with a bare
# `python3`. That is correct on macOS / Linux but frequently WRONG on
# native Windows: the python.org installer ships `python.exe` + the `py`
# launcher, not `python3`. So we DETECT a working Python 3.9+ launcher on
# the host OS at install time and render hooks.json with it. The runner
# itself (hook_runner.py) is pure stdlib and identical on every OS.
# ---------------------------------------------------------------------------

HOOK_RUNNER_REL = ".cursor/hooks/scripts/hook_runner.py"
_HOOK_INTERPRETER_CACHE: "str | None" = None


def _probe_python(cmd: list[str]) -> bool:
    """True if `cmd` resolves to a Python interpreter >= 3.9."""
    exe = shutil.which(cmd[0])
    if not exe:
        return False
    try:
        probe = subprocess.run(
            [exe, *cmd[1:], "-c",
             "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return False
    return probe.returncode == 0


def detect_hook_interpreter() -> str:
    """Return the command prefix (e.g. ``py -3`` or ``python3``) that the
    installed hooks.json should use to launch the Python runner on THIS
    host. Probes real candidates in OS-appropriate order and verifies
    each resolves to Python 3.9+, falling back to the absolute path of
    the interpreter running this script."""
    global _HOOK_INTERPRETER_CACHE
    if _HOOK_INTERPRETER_CACHE is not None:
        return _HOOK_INTERPRETER_CACHE
    if os.name == "nt":
        candidates = [["py", "-3"], ["python"], ["python3"]]
    else:
        candidates = [["python3"], ["python"]]
    chosen = ""
    for cand in candidates:
        if _probe_python(cand):
            chosen = " ".join(cand)
            break
    if not chosen:
        exe = sys.executable or "python3"
        chosen = f'"{exe}"' if " " in exe else exe
    _HOOK_INTERPRETER_CACHE = chosen
    return chosen


def render_hooks_json(pack_hooks_json: Path, interpreter: str) -> str:
    """Render a project's .cursor/hooks.json from the pack template,
    rewriting every hook command so it launches the Python runner with
    `interpreter`. Preserves loop_limit and any other keys verbatim."""
    data = json.loads(pack_hooks_json.read_text(encoding="utf-8"))
    for entries in (data.get("hooks") or {}).values():
        for entry in entries:
            cmd = entry.get("command", "")
            idx = cmd.find(HOOK_RUNNER_REL)
            if idx == -1:
                continue
            entry["command"] = f"{interpreter} {cmd[idx:]}"
    return json.dumps(data, indent=2) + "\n"


def _is_hooks_json_op(op: "UpgradeOp") -> bool:
    return op.reason == "hooks" and op.target.name == "hooks.json"


@dataclass
class UpgradeOp:
    source: Path
    target: Path
    reason: str
    action: str = "copy"  # copy | create | skip | refused
    # True only when this run actually wrote the target to disk. Stays
    # False on dry-runs, so the report can't claim phantom writes.
    applied: bool = False


@dataclass
class UpgradeReport:
    pack_version: str = ""
    previous_version: str = ""
    operations: list[UpgradeOp] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot_path: str = ""

    def summary(self) -> dict:
        changed = sum(1 for op in self.operations if op.action != "skip")
        return {
            "pack_version": self.pack_version,
            "previous_version": self.previous_version,
            "ops": len(self.operations),
            "would_change": changed,
            "errors": len(self.errors),
        }


# ---------------------------------------------------------------------------
# Version utilities
# ---------------------------------------------------------------------------


def read_pack_version(pack_root: Path) -> str:
    vf = pack_root / "VERSION"
    if not vf.exists():
        return ""
    return vf.read_text(encoding="utf-8").strip()


def read_project_version(project_root: Path) -> dict:
    p = project_root / ".cursor" / "pack-version.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_project_version(project_root: Path, pack_version: str) -> None:
    p = project_root / ".cursor" / "pack-version.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pack_version": pack_version,
        "integrated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# .gitignore hardening (memory files must never leak)
# ---------------------------------------------------------------------------

GITIGNORE_BLOCK_MARKER = "# harmonist: memory privacy"
GITIGNORE_LINES = [
    GITIGNORE_BLOCK_MARKER,
    ".cursor/memory/*.md",
    "!.cursor/memory/*.shared.md",
    "!.cursor/memory/README.md",
    "!.cursor/memory/SCHEMA.md",
    ".cursor/hooks/.state/",
    ".cursor/hooks/config.json.local",
    ".cursor/telemetry/",
    ".cursor/.integration-snapshots/",
    ".cursor/repomap/*.db",
]


def ensure_gitignore(project_root: Path, apply: bool) -> tuple[bool, bool]:
    """Make sure .gitignore excludes memory files. Returns (changed, created)."""
    gi = project_root / ".gitignore"
    # USER-owned file: strict UTF-8 so a Windows locale default can't
    # mojibake the existing content when we append our block below.
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if GITIGNORE_BLOCK_MARKER in existing:
        return (False, False)
    created = not gi.exists()
    block = "\n".join(GITIGNORE_LINES) + "\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    new_content = existing + ("\n" if existing else "") + block
    if apply:
        gi.write_text(new_content, encoding="utf-8")
    return (True, created)


def compare_versions(a: str, b: str) -> int:
    """Return -1/0/1 for semver a vs b. Ignores pre-release suffixes."""
    def norm(v: str) -> tuple[int, int, int]:
        parts = v.split("-", 1)[0].split(".")
        nums = [int(x) for x in parts if x.isdigit()]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:3])
    ax, bx = norm(a), norm(b)
    return (ax > bx) - (ax < bx)


# ---------------------------------------------------------------------------
# Pack-owned file plan
# ---------------------------------------------------------------------------


def pack_owned_plan(pack_root: Path, project_root: Path) -> list[UpgradeOp]:
    """Enumerate every file the upgrade is allowed to overwrite."""
    ops: list[UpgradeOp] = []

    # 1. Strict agents (orchestration + 5 reviewers). Each copied into
    #    .cursor/agents/.  Bg-regression excluded (project-customised body).
    agents_src_root = pack_root / "agents"
    agents_tgt = project_root / ".cursor" / "agents"
    for rel in PACK_OWNED_STRICT_SLUGS:
        src = agents_src_root / rel
        tgt = agents_tgt / Path(rel).name
        ops.append(UpgradeOp(src, tgt, reason="strict-agent"))

    # 2. Hook scripts + config.
    #    hook_runner.py is the cross-platform (incl. native Windows) active
    #    path that hooks.json invokes; the .sh scripts are the POSIX
    #    equivalents kept for hooks.posix.json and the shell test harness.
    for rel in [
        "hooks.json",
        "scripts/hook_runner.py",
        "scripts/lib.sh",
        "scripts/seed-session.sh",
        "scripts/record-write.sh",
        "scripts/record-subagent-start.sh",
        "scripts/record-subagent-stop.sh",
        "scripts/gate-stop.sh",
        "scripts/gate-shell.sh",
        "scripts/git-pre-commit.sh",
        "scripts/install-git-hooks.sh",
    ]:
        src = pack_root / "hooks" / rel
        tgt = project_root / ".cursor" / ("hooks.json" if rel == "hooks.json" else f"hooks/{rel}")
        ops.append(UpgradeOp(src, tgt, reason="hooks"))

    # 3. Memory tooling (CLI + validator + migrations + schema docs).
    #    Template markdown files are excluded -- they contain real project
    #    history.
    for rel in ["memory.py", "validate.py", "migrations.py", "SCHEMA.md", "README.md"]:
        src = pack_root / "memory" / rel
        tgt = project_root / ".cursor" / "memory" / rel
        ops.append(UpgradeOp(src, tgt, reason="memory-tooling"))

    # 3b. Repo map engine (zero-dep code index). Installed alongside memory;
    #     the index DB it builds (.cursor/repomap/graph.db) is gitignored.
    ops.append(UpgradeOp(
        pack_root / "agents" / "scripts" / "repomap.py",
        project_root / ".cursor" / "repomap" / "repomap.py",
        reason="repomap-tooling"))

    # 4. Pack-owned Cursor rule: protocol-enforcement.mdc. The canonical
    #    template carries a `<!-- pack-owned: protocol-enforcement v1 -->`
    #    marker. Refresh only if the existing file ALSO carries the marker
    #    (i.e. it's a prior pack copy). If the project has a custom
    #    `protocol-enforcement.mdc` without the marker we skip with a
    #    warning so user content is never clobbered -- scan_rules_conflicts
    #    will flag it separately.
    rule_src = pack_root / "agents" / "templates" / "rules" / "protocol-enforcement.mdc"
    rule_tgt = project_root / ".cursor" / "rules" / "protocol-enforcement.mdc"
    if rule_src.exists():
        if not rule_tgt.exists():
            ops.append(UpgradeOp(rule_src, rule_tgt,
                                 reason="cursor-rule-protocol (initial)"))
        else:
            existing = rule_tgt.read_text(encoding="utf-8", errors="replace")
            if "pack-owned: protocol-enforcement" in existing:
                ops.append(UpgradeOp(rule_src, rule_tgt,
                                     reason="cursor-rule-protocol"))
            # else: user-customised without marker -- leave alone

    return ops


# ---------------------------------------------------------------------------
# Core upgrade logic
# ---------------------------------------------------------------------------


def _same_bytes(a: Path, b: Path) -> bool:
    if not (a.exists() and b.exists()):
        return False
    return a.read_bytes() == b.read_bytes()


def _render_diff(src: Path, tgt: Path, width: int = 4) -> str:
    s = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if src.exists() else []
    t = tgt.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if tgt.exists() else []
    return "".join(
        difflib.unified_diff(
            t, s, fromfile=str(tgt), tofile=str(src), n=width,
        )
    )


def locate_pack_root(cli_value: Path | None, project_root: Path) -> Path | None:
    """Find the pack root.

    Preference:
      1. explicit --pack
      2. relative to this script (normal case when running from inside the pack)
      3. `harmonist/` next to the project's AGENTS.md
    """
    if cli_value:
        return cli_value.resolve()
    here = Path(__file__).resolve().parent.parent.parent  # agents/scripts -> pack root
    if (here / "VERSION").exists():
        return here
    alt = project_root / "harmonist"
    if (alt / "VERSION").exists():
        return alt
    return None


def plan_upgrade(pack_root: Path, project_root: Path) -> UpgradeReport:
    report = UpgradeReport(
        pack_version=read_pack_version(pack_root),
        previous_version=read_project_version(project_root).get("pack_version", ""),
    )
    for op in pack_owned_plan(pack_root, project_root):
        if not op.source.exists():
            report.errors.append(f"pack source missing: {op.source}")
            continue
        if _is_hooks_json_op(op):
            # hooks.json is RENDERED (interpreter rewritten for the host
            # OS), not byte-copied -- so idempotency must compare against
            # the rendered output, otherwise every re-apply looks dirty.
            rendered = render_hooks_json(op.source, detect_hook_interpreter())
            if not op.target.exists():
                op.action = "create"
            elif op.target.read_text(encoding="utf-8") == rendered:
                op.action = "skip"
            else:
                op.action = "copy"
            report.operations.append(op)
            continue
        if not op.target.exists():
            op.action = "create"
        elif _same_bytes(op.source, op.target):
            op.action = "skip"
        else:
            op.action = "copy"
        report.operations.append(op)
    return report


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(pack_root: Path) -> dict[str, str]:
    """Return {relpath: sha256} from the pack's MANIFEST.sha256.
    Empty dict if missing (caller decides whether to refuse)."""
    mf = pack_root / "MANIFEST.sha256"
    if not mf.exists():
        return {}
    out: dict[str, str] = {}
    for raw in mf.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sha, rel = parts
        if len(sha) != 64:
            continue
        out[rel] = sha
    return out


def _verify_source_integrity(op: "UpgradeOp", pack_root: Path,
                             manifest: dict[str, str]) -> str:
    """Return empty string if OK, a non-empty reason otherwise."""
    if not manifest:
        return ""  # no manifest -> best-effort; caller logs a warning once
    try:
        rel = op.source.resolve().relative_to(pack_root.resolve()).as_posix()
    except ValueError:
        return ""  # source outside pack root (shouldn't happen)
    expected = manifest.get(rel)
    if expected is None:
        return ""  # source not in manifest -> not a tampering signal
    actual = _sha256_of(op.source)
    if actual != expected:
        return (f"{rel}: manifest expected {expected[:12]}..., "
                f"actual {actual[:12]}... -- possible supply-chain tampering")
    return ""


SNAPSHOT_DIR_NAME = ".integration-snapshots"


def _snapshot_root(project_root: Path) -> Path:
    return project_root / ".cursor" / SNAPSHOT_DIR_NAME


def _take_snapshot(report: UpgradeReport, project_root: Path) -> Path | None:
    """Tar.gz every target file that currently exists and would be
    modified, and record the list of files this apply is about to
    CREATE. Returns the snapshot tarball path, or None if there's
    nothing to do.

    Two pieces are persisted:
      - `snapshot-<ts>.tar.gz` — pre-apply contents of files that
        already existed and would be modified + pack-owned side files
        (pack-version.json, pack-manifest.json, .gitignore, AGENTS.md).
      - `snapshot-<ts>.json`  — metadata: file_count, files (sha256
        per path), and `creations` (paths the apply is about to create
        from scratch; rollback deletes these)."""
    import tarfile, time
    to_snapshot: list[Path] = []
    creations: list[str] = []
    proj_resolved = project_root.resolve()

    def _rel(p: Path) -> str | None:
        try:
            return str(p.resolve().relative_to(proj_resolved))
        except ValueError:
            return None

    for op in report.operations:
        if op.action == "skip":
            continue
        if op.target.exists():
            to_snapshot.append(op.target)
        else:
            rel = _rel(op.target)
            if rel is not None:
                creations.append(rel)

    # Side files that the apply writes / mutates but aren't in the
    # `report.operations` list (pack-version.json, pack-manifest.json,
    # .gitignore block). Snapshot the ones that already exist so they
    # can be restored; record the ones that don't so rollback removes
    # them if they didn't exist before.
    for side in [
        project_root / ".cursor" / "pack-version.json",
        project_root / ".cursor" / "pack-manifest.json",
        project_root / ".gitignore",
        project_root / "AGENTS.md",
    ]:
        if side.exists():
            if side not in to_snapshot:
                to_snapshot.append(side)
        else:
            rel = _rel(side)
            if rel is not None and rel not in creations:
                creations.append(rel)

    if not to_snapshot and not creations:
        return None

    snap_dir = _snapshot_root(project_root)
    snap_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision so two --apply runs in the same wallclock
    # second still get distinct (and chronologically-sortable!) names.
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    us = int((time.time() % 1) * 1_000_000)
    tarball = snap_dir / f"snapshot-{ts}Z{us:06d}.tar.gz"
    # Extra defensive: if two calls land on the exact same microsecond
    # (rare), add a counter. Lexical order is preserved either way.
    n = 1
    while tarball.exists():
        tarball = snap_dir / f"snapshot-{ts}Z{us:06d}_{n:02d}.tar.gz"
        n += 1
    manifest: dict[str, str] = {}
    with tarfile.open(tarball, "w:gz") as tar:
        for p in to_snapshot:
            arc = _rel(p)
            if arc is None:
                continue
            tar.add(str(p), arcname=arc)
            manifest[arc] = _sha256_of(p)
    meta = {
        "created_at":   ts,
        "project":      str(proj_resolved),
        "pack_version": report.pack_version,
        "file_count":   len(manifest),
        "files":        manifest,
        "creations":    sorted(set(creations)),
    }
    meta_path = snap_dir / (tarball.stem.replace(".tar", "") + ".json")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    return tarball


def apply_plan(report: UpgradeReport, dry_run: bool,
               pack_root: Path | None = None,
               project_root: Path | None = None,
               snapshot: bool = True) -> None:
    """Copy files, guarded by MANIFEST.sha256. Any source whose sha
    doesn't match the manifest is refused (appended to report.errors)
    and NOT copied. If MANIFEST is missing entirely we fall back to
    best-effort copy and add a single warning."""
    manifest: dict[str, str] = {}
    if pack_root is not None:
        manifest = _load_manifest(pack_root)
        if not manifest:
            report.errors.append(
                "MANIFEST.sha256 not found in pack -- "
                "cannot verify source integrity; proceeding without check. "
                "Regenerate with: python3 agents/scripts/build_manifest.py",
            )

    # Pre-apply snapshot so --rollback can undo this run.
    if not dry_run and snapshot and project_root is not None:
        try:
            tarball = _take_snapshot(report, project_root)
            if tarball is not None:
                report.snapshot_path = str(tarball.resolve())
        except Exception as e:
            report.errors.append(
                f"could not create pre-apply snapshot: {e.__class__.__name__}: {e}")

    for op in report.operations:
        if op.action == "skip":
            continue
        if dry_run:
            continue
        if pack_root is not None and manifest:
            problem = _verify_source_integrity(op, pack_root, manifest)
            if problem:
                report.errors.append(f"REFUSED: {problem}")
                op.action = "refused"
                continue
        op.target.parent.mkdir(parents=True, exist_ok=True)
        if _is_hooks_json_op(op):
            # Render with a host-appropriate Python launcher rather than
            # copying the pack's bare `python3` command verbatim.
            op.target.write_text(
                render_hooks_json(op.source, detect_hook_interpreter()),
                encoding="utf-8")
        else:
            shutil.copy2(op.source, op.target)
        op.applied = True

    # Write .cursor/pack-manifest.json so we can detect post-install
    # drift (someone edits security-reviewer.md to weaken the gate).
    if not dry_run and pack_root is not None and manifest:
        try:
            _write_installed_manifest(report, pack_root, manifest)
        except Exception as e:
            report.errors.append(
                f"could not write .cursor/pack-manifest.json: {e}")


def list_snapshots(project_root: Path) -> list[Path]:
    snap_dir = _snapshot_root(project_root)
    if not snap_dir.exists():
        return []
    return sorted(snap_dir.glob("snapshot-*.tar.gz"))


def rollback(project_root: Path, which: str | None = None) -> "UpgradeReport":
    """Restore files from a previously-taken snapshot.

    `which`:
      - None          -> most recent snapshot
      - 'list'        -> print available snapshots and exit
      - explicit path -> restore from that tarball
    Returns an UpgradeReport describing what was touched, for CLI
    rendering."""
    import tarfile
    report = UpgradeReport(
        pack_version=read_pack_version(project_root / "harmonist")
        if (project_root / "harmonist" / "VERSION").exists() else "",
        previous_version=read_project_version(project_root).get("pack_version", ""),
    )
    snaps = list_snapshots(project_root)
    if not snaps:
        report.errors.append(
            f"no snapshots found under {_snapshot_root(project_root)}")
        return report

    target_tar: Path
    if which is None:
        target_tar = snaps[-1]
    else:
        target_tar = Path(which)
        if not target_tar.is_absolute():
            target_tar = (_snapshot_root(project_root) / which).resolve()
        if not target_tar.exists():
            report.errors.append(f"snapshot not found: {target_tar}")
            return report

    # Load sibling meta file (records `creations` = files the apply
    # was about to create from scratch).
    meta_path = target_tar.with_name(
        target_tar.stem.replace(".tar", "") + ".json")
    creations: list[str] = []
    if meta_path.exists():
        try:
            creations = json.loads(meta_path.read_text(encoding="utf-8")).get("creations", [])
        except Exception:
            creations = []

    restored: list[str] = []
    with tarfile.open(target_tar, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            out = project_root / member.name
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                out.write_bytes(extracted.read())
                restored.append(member.name)
            except Exception as e:
                report.errors.append(f"restore failed for {member.name}: {e}")

    # Delete files that the apply CREATED (recorded at snapshot time).
    removed: list[str] = []
    for rel in creations:
        p = project_root / rel
        if p.exists() and p.is_file():
            try:
                p.unlink()
                removed.append(rel)
            except Exception as e:
                report.errors.append(f"could not remove created file {rel}: {e}")

    ops: list[UpgradeOp] = []
    for f in restored:
        ops.append(UpgradeOp(
            source=target_tar, target=project_root / f, reason="rollback",
            action="copy",
        ))
    for f in removed:
        ops.append(UpgradeOp(
            source=target_tar, target=project_root / f,
            reason="undo-creation", action="refused",
        ))
    report.operations = ops
    report.snapshot_path = str(target_tar)
    return report


def _write_installed_manifest(report: UpgradeReport, pack_root: Path,
                              manifest: dict[str, str]) -> None:
    """Record sha256 of each pack-owned file that ended up in the
    project, so `verify_integration` can detect later tampering."""
    installed: dict[str, str] = {}
    for op in report.operations:
        if op.action in ("skip", "refused"):
            continue
        if not op.target.exists():
            continue
        try:
            rel_target = op.target.resolve().relative_to(
                _project_root_from_op(op)).as_posix()
        except Exception:
            rel_target = op.target.name
        if _is_hooks_json_op(op):
            # hooks.json is rendered per-host, so the source sha would be
            # wrong. Record the sha of what actually landed on disk.
            expected = _sha256_of(op.target)
        else:
            try:
                src_rel = op.source.resolve().relative_to(pack_root.resolve()).as_posix()
                expected = manifest.get(src_rel)
                if expected is None:
                    expected = _sha256_of(op.source)
            except Exception:
                expected = _sha256_of(op.source)
        installed[rel_target] = expected

    if not installed:
        return
    # Write into the project root, inferred from first operation target.
    first = next((op for op in report.operations if op.action != "skip"), None)
    if first is None:
        return
    proj_root = _project_root_from_op(first)
    target = proj_root / ".cursor" / "pack-manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    import time
    payload = {
        "pack_version": report.pack_version,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": installed,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")


def _project_root_from_op(op: "UpgradeOp") -> Path:
    """Infer the project root from a pack-owned op. Targets live under
    `<project>/.cursor/...` or `<project>/AGENTS.md`."""
    t = op.target.resolve()
    parts = t.parts
    if ".cursor" in parts:
        idx = parts.index(".cursor")
        return Path(*parts[:idx])
    # AGENTS.md, .gitignore at project root.
    return t.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def render_report(report: UpgradeReport, mode: str, show_diff: bool) -> str:
    lines: list[str] = []
    lines.append(
        f"  pack version: {report.pack_version or '<unknown>'}   "
        f"project version: {report.previous_version or '<never integrated>'}"
    )
    lines.append("")
    counts = {"copy": 0, "create": 0, "skip": 0, "refused": 0}
    for op in report.operations:
        counts[op.action] = counts.get(op.action, 0) + 1
        if op.action == "skip":
            continue
        icon = {"copy": "~", "create": "+", "refused": "!"}.get(op.action, "?")
        lines.append(f"  {icon} {op.target}   ({op.reason})")
        if show_diff and op.action == "copy":
            diff = _render_diff(op.source, op.target)
            if diff:
                for dl in diff.splitlines():
                    lines.append(f"      {dl}")
    if not any(op.action != "skip" for op in report.operations):
        lines.append("  (no pack-owned files need refreshing)")
    lines.append("")
    lines.append(
        f"  Summary: create={counts.get('create', 0)} copy={counts.get('copy', 0)} "
        f"skip={counts.get('skip', 0)} refused={counts.get('refused', 0)}   "
        f"errors={len(report.errors)}"
    )
    for e in report.errors:
        lines.append(f"  ERROR {e}")
    if mode == "dry-run":
        lines.append("  (dry-run: nothing written. Re-run with --apply to perform the upgrade.)")
    elif mode == "apply":
        changed = sum(1 for op in report.operations if op.applied)
        lines.append(f"  Applied: {changed} file(s) refreshed.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project root to upgrade. Default: current directory.")
    ap.add_argument("--pack", type=Path,
                    help="Explicit path to the pack's root (containing VERSION + agents/). "
                         "Default: auto-detect.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually copy files. Default is dry-run.")
    ap.add_argument("--diff", action="store_true",
                    help="Show unified diffs for every file that would change.")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON output.")
    ap.add_argument("--rollback", action="store_true",
                    help="Restore the most recent pre-apply snapshot (or the "
                         "one named via --snapshot) and remove files this "
                         "integration introduced.")
    ap.add_argument("--snapshot", type=str, default=None,
                    help="When used with --rollback, name of the snapshot "
                         "file to restore (defaults to most recent).")
    ap.add_argument("--list-snapshots", action="store_true",
                    help="List available integration snapshots and exit.")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="Skip taking a pre-apply snapshot (rollback will be "
                         "impossible for this run). Not recommended.")
    args = ap.parse_args(argv)

    project_root = args.project.resolve()

    # --list-snapshots is a read-only info command.
    if args.list_snapshots:
        snaps = list_snapshots(project_root)
        if not snaps:
            print(f"  no snapshots in {_snapshot_root(project_root)}")
            return 0
        for s in snaps:
            meta = s.with_suffix(".json").with_suffix("")
            meta_path = s.with_name(s.name.replace(".tar.gz", ".json"))
            meta_data = {}
            if meta_path.exists():
                try:
                    meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            fc = meta_data.get("file_count", "?")
            pv = meta_data.get("pack_version", "?")
            print(f"  {s.name}   files={fc}   pack={pv}")
        return 0

    # --rollback does not need an upgrade plan or pack.
    if args.rollback:
        r = rollback(project_root, which=args.snapshot)
        if args.json:
            print(json.dumps({**r.summary(), "snapshot": r.snapshot_path,
                               "errors": r.errors}, indent=2))
        else:
            if r.snapshot_path:
                print(f"  Rolled back from: {r.snapshot_path}")
            for op in r.operations:
                icon = {"copy": "~", "refused": "-"}.get(op.action, "?")
                print(f"  {icon} {op.target}")
            for e in r.errors:
                print(f"  ERROR {e}")
            if r.errors:
                return 1
            changed = len(r.operations)
            print(f"  Restored {changed} file(s).")
        return 0 if not r.errors else 1

    if not (project_root / "AGENTS.md").exists():
        print(f"upgrade: no AGENTS.md in {project_root}. Integrate first.", file=sys.stderr)
        return 2
    pack_root = locate_pack_root(args.pack, project_root)
    if pack_root is None:
        print("upgrade: cannot locate the pack source. Pass --pack <path>.", file=sys.stderr)
        return 3

    report = plan_upgrade(pack_root, project_root)

    # Refuse a downgrade unless user forces it (not implemented yet).
    if report.previous_version and report.pack_version:
        cmp = compare_versions(report.pack_version, report.previous_version)
        if cmp < 0 and args.apply:
            print(
                f"upgrade: pack version {report.pack_version} is older than "
                f"project's recorded {report.previous_version}. Refusing to downgrade.",
                file=sys.stderr,
            )
            return 2

    mode = "apply" if args.apply else "dry-run"
    apply_plan(report, dry_run=not args.apply, pack_root=pack_root,
               project_root=project_root, snapshot=not args.no_snapshot)

    # If bg-regression-runner is missing OR looks like the untouched
    # template, seed it from the detected project commands. A file that
    # already contains real test commands is left alone.
    try:
        from detect_regression_commands import detect_all, render_bg_regression  # noqa: E402
        bg_path = project_root / ".cursor" / "agents" / "bg-regression-runner.md"
        needs_seeding = True
        if bg_path.exists():
            body = bg_path.read_text(encoding="utf-8", errors="replace")
            placeholder_markers = ("<PROJECT_TEST_CMD>", "<replace this>", "[CUSTOMIZE",
                                   "TBD", "your project's test command")
            already_customised = any(m.lower() in body.lower() for m in placeholder_markers) is False
            # If a known runner already appears in the body, assume customised.
            known_runners = ("pytest", "vitest", "jest", "mvn test", "gradle", "cargo test",
                             "go test", "npm test", "pnpm test", "yarn test", "rspec", "mix test")
            if any(r in body for r in known_runners):
                already_customised = True
            needs_seeding = not already_customised
        if needs_seeding:
            pack_src = pack_root / "agents" / "review" / "bg-regression-runner.md"
            if pack_src.exists():
                template = pack_src.read_text(encoding="utf-8")
                cmds = detect_all(project_root)
                block = render_bg_regression(cmds)
                # Drop the new block just before the final newline of the template.
                seeded = template.rstrip() + "\n\n" + block
                existed_before = bg_path.exists()
                if args.apply:
                    bg_path.parent.mkdir(parents=True, exist_ok=True)
                    bg_path.write_text(seeded, encoding="utf-8")
                report.operations.append(UpgradeOp(
                    source=pack_src, target=bg_path,
                    reason=f"bg-regression-runner seeded from detected manifests "
                           f"(tests={cmds['test'] or '-'}, lint={cmds['lint'] or '-'})",
                    action="copy" if existed_before else "create",
                    applied=args.apply,
                ))
    except Exception as e:
        report.errors.append(f"bg-regression auto-config crashed: {e.__class__.__name__}: {e}")

    # Ensure .gitignore hides memory files so nobody accidentally commits
    # session state. Opt-out anchors are *.shared.md + README.md + SCHEMA.md.
    try:
        changed, created = ensure_gitignore(project_root, apply=args.apply)
        if changed:
            report.operations.append(UpgradeOp(
                source=pack_root / "agents" / "scripts" / "upgrade.py",
                target=project_root / ".gitignore",
                reason="gitignore: memory-privacy block " + ("added" if not created else "file created"),
                # Action stays the would-change plan ("create"/"copy");
                # `applied` records whether bytes actually hit disk.
                action="create" if created else "copy",
                applied=args.apply,
            ))
    except Exception as e:
        report.errors.append(f"gitignore hardening crashed: {e.__class__.__name__}: {e}")

    # Splice pack-owned sections from the pack's AGENTS template
    # (AGENTS.template.md; legacy packs: AGENTS.md) into the project's
    # AGENTS.md, rewriting literal `harmonist/` path prefixes to the
    # actual pack dir name. Non-fatal: if the project's AGENTS.md predates
    # markers, the merger refuses with a clear error and we continue.
    try:
        pack_tpl = pack_template_path(pack_root)
        mr = merge_agents_md_fn(
            pack_tpl, project_root / "AGENTS.md",
            pack_dir=pack_dir_relname(pack_root, project_root))
        if mr.errors:
            report.errors.extend(mr.errors)
        elif mr.replaced or mr.inserted:
            if args.apply:
                # USER-owned file: strict UTF-8 (cp1252 would mojibake it).
                (project_root / "AGENTS.md").write_text(mr.output,
                                                        encoding="utf-8")
            report.operations.append(UpgradeOp(
                source=pack_tpl,
                target=project_root / "AGENTS.md",
                reason=f"AGENTS.md pack-owned merge (replaced={mr.replaced}, inserted={mr.inserted})",
                action="copy",
                applied=args.apply,
            ))
    except Exception as e:
        report.errors.append(f"merge_agents_md crashed: {e.__class__.__name__}: {e}")

    # Record the new version on a successful apply (even if nothing actually
    # changed -- this updates integrated_at).
    if args.apply and not report.errors:
        write_project_version(project_root, report.pack_version)

    if args.json:
        payload = {
            "mode": mode,
            **report.summary(),
            "operations": [
                {"source": str(op.source), "target": str(op.target),
                 "reason": op.reason, "action": op.action,
                 "applied": op.applied}
                for op in report.operations
            ],
            "errors": report.errors,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(report, mode, show_diff=args.diff))

    if report.errors:
        return 2
    changed = sum(1 for op in report.operations if op.action != "skip")
    return 1 if (args.apply and changed > 0) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
