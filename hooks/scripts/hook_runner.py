#!/usr/bin/env python3
"""
hook_runner.py -- cross-platform Python implementation of every
harmonist enforcement hook.

Why this exists:
  Cursor hooks on macOS / Linux / WSL happily run the bash versions
  under `scripts/*.sh`. Windows-native Cursor does not have bash by
  default, which made the whole enforcement layer POSIX-only. This
  module reimplements the full state machine in stdlib Python so the
  hooks work identically on every OS Cursor runs on.

Invocation:
  python3 hook_runner.py sessionStart       < stdin_json
  python3 hook_runner.py afterFileEdit      < stdin_json
  python3 hook_runner.py subagentStart      < stdin_json
  python3 hook_runner.py subagentStop       < stdin_json
  python3 hook_runner.py stop               < stdin_json

Each subcommand:
  * reads the hook input JSON on stdin
  * mutates the shared state file at <hooks_dir>/.state/session.json
  * bumps telemetry counters in <telemetry_dir>/agent-usage.json
  * prints the hook response JSON on stdout

Behaviour is bit-for-bit compatible with the .sh equivalents -- every
scenario in hooks/tests/run-hook-tests.sh continues to pass whether
the shell or Python path is in use.

Env overrides (identical to lib.sh):
  AGENT_PACK_MEMORY_CLI   path to memory.py (tests pin this)
  AGENT_PACK_HOOKS_STATE  explicit session.json path
  AGENT_PACK_TELEMETRY_DIR  override telemetry dir
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

import calendar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --------------------------------------------------------------------------- layout

SCRIPT_DIR = Path(__file__).resolve().parent
HOOKS_DIR = SCRIPT_DIR.parent
STATE_DIR = HOOKS_DIR / ".state"
STATE_FILE = STATE_DIR / "session.json"
INCIDENTS_FILE = STATE_DIR / "incidents.json"
CFG_FILE = HOOKS_DIR / "config.json"
LOG_FILE = STATE_DIR / "activity.log"


def _resolve_telemetry_dir() -> Path:
    env = os.environ.get("AGENT_PACK_TELEMETRY_DIR")
    if env:
        return Path(env)
    return (HOOKS_DIR.parent / "telemetry").resolve()


TELEMETRY_DIR = _resolve_telemetry_dir()
TELEMETRY_FILE = TELEMETRY_DIR / "agent-usage.json"

STATE_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- config

# Conservative patterns for genuinely destructive / high-risk shell commands.
# The beforeShellExecution hook asks for human confirmation (HITL) before
# these run. Tuned to catch catastrophic intent (root/home/wildcard deletes,
# force-push, disk wipes, fork bombs, pipe-to-shell) while leaving routine
# commands (e.g. `rm -rf node_modules`) alone.
DEFAULT_DANGEROUS_COMMAND_PATTERNS = [
    # rm with a recursive/force flag (short -rf/-fr or long
    # --recursive/--force/--no-preserve-root, in ANY order) targeting an
    # absolute path, home, wildcard, or '.'. Matches the catastrophic forms
    # the old single-dash pattern missed: `rm -rf /usr`, `rm --recursive
    # --force /`, `rm -rf --no-preserve-root /` -- and tolerates a quoted
    # target (`rm -rf "$HOME"`, `rm -rf "/"`).
    r"(?:^|[\s;&|(])rm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*|--recursive|--force|--no-preserve-root)(?:\s+(?:-[a-zA-Z-]+|--[a-zA-Z-]+))*\s+[\"']?(?:/(?:\s|$|/|\*|[a-zA-Z\"'])|~|\$HOME|\*|\.(?:\s|$|/))",
    r"\bgit\s+push\b[^\n]*(?:--force(?!-with-lease)|\s-f\b)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    # dd writing to a raw device (optionally quoted): `dd if=x of=/dev/sda`,
    # `dd of=/dev/sda if=/dev/zero`. Restricted to of=/dev/... -- a dd that
    # merely READS a device or writes a regular file (`of=/tmp/img`) is a
    # routine backup/restore, not a disk wipe. `/+` tolerates repeated
    # slashes (`of=//dev/sda` resolves to the same device node).
    r"\bdd\s+[^\n]*\bof=[\"']?/+dev/",
    r"\bmkfs[.\s]",
    r":\(\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:",
    r"\b(?:shutdown|reboot|halt|poweroff)\b",
    r"\bchmod\s+-R\s+0?777\b",
    r">\s*/dev/(?:sd|nvme|disk|hd)",
    r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b",
    # Case-insensitive: `drop table users` is exactly as destructive as
    # `DROP TABLE users`.
    r"(?i)\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
]


DEFAULT_CFG = {
    "require_qa_verifier": True,
    "require_any_reviewer": True,
    "require_session_handoff_update": True,
    "telemetry_enabled": True,
    "skip_path_patterns": [
        r"^\.cursor/",
        r"^\.git/",
        r"^node_modules/",
        r"^\.venv/",
        r"^dist/",
        r"^build/",
        r"^target/",
        r"^coverage/",
    ],
    "memory_paths": [
        ".cursor/memory/session-handoff.md",
        ".cursor/memory/decisions.md",
        ".cursor/memory/patterns.md",
    ],
    "reviewer_slugs": [
        "qa-verifier",
        "security-reviewer",
        "code-quality-auditor",
        "sre-observability",
        "bg-regression-runner",
        # On-demand strict review gate (installed when a project has UI):
        # credited as a reviewer when the orchestrator invokes it on a
        # frontend/accessibility change.
        "wcag-a11y-gate",
    ],
    "required_reviewer_slug": "qa-verifier",
    "require_regression_passed": False,
    "allow_trivial_without_review": True,
    "trivial_path_patterns": [
        r"(?i)\.md$",
        r"(?i)\.mdx$",
        r"(?i)\.rst$",
        r"(?i)\.txt$",
        r"(?i)(^|/)README($|[\-.])",
        r"(?i)(^|/)CHANGELOG($|[\-.])",
        r"(?i)(^|/)LICEN[CS]E($|[\-.])",
        r"(?i)(^|/)NOTICE($|[\-.])",
        r"(?i)(^|/)\.gitignore$",
        r"(?i)(^|/)\.editorconfig$",
        r"(?i)(^|/)\.prettier(rc|ignore)[\-.]?",
        r"(?i)(^|/)\.eslint(rc|ignore)[\-.]?",
        # docs/ and documentation/ are only "trivial" for documentation /
        # asset content -- NOT for code that happens to live under them
        # (e.g. docs/conf.py or src/docs/handler.py), which must still be
        # reviewed. The bare substring `docs/` let any such code file skip
        # the gate (fail-open).
        r"(?i)(^|/)docs/.*\.(?:md|mdx|rst|txt|adoc|markdown|png|jpe?g|gif|svg|webp|ico|pdf)$",
        r"(?i)(^|/)documentation/.*\.(?:md|mdx|rst|txt|adoc|markdown|png|jpe?g|gif|svg|webp|ico|pdf)$",
    ],
    "protocol_skip_warn_enabled": True,
    "protocol_skip_warn_threshold_count": 5,
    "protocol_skip_warn_threshold_ratio": 0.25,
    "loop_limit": 3,
    # Mechanical cap on how many subagents may run CONCURRENTLY within one
    # task. Unbounded parallel fan-out (mesh topology) can spawn dozens of
    # heavyweight subagents at once and exhaust RAM -- especially now that
    # agents run on a 1M-context model. The subagentStart hook denies a
    # launch once this many subagents are already open. Set to 0 (or a
    # negative number) to disable the cap.
    "max_concurrent_subagents": 3,
    # A subagent whose start is older than this many seconds with no
    # observed stop is treated as finished for the purpose of the
    # concurrency count, so a missed subagentStop can never permanently
    # lock out new launches.
    "subagent_stale_seconds": 900,
    # Impact-aware gate (opt-in, like require_regression_passed). When on,
    # the stop hook uses the repo map to compute the test files affected by
    # this task's edits and refuses to finish until a regression run has
    # passed (last_regression_ok) when affected tests exist.
    "require_affected_tests": False,
    # Warn at sessionStart when the repo map is stale (files changed since
    # the last build/refresh). Purely informational; never blocks.
    "repomap_staleness_warn": True,
    # Delegation-context gate (opt-in). When on, subagentStart denies a
    # delegation whose handoff text (prompt minus the AGENT: marker) is
    # shorter than min_delegation_chars -- forcing the orchestrator to pass
    # real context (target/scope/sub-goal/success) instead of making the
    # subagent guess and redo work.
    "require_delegation_context": False,
    "min_delegation_chars": 80,
    # Human-in-the-loop on dangerous shell commands. The beforeShellExecution
    # hook matches the command against dangerous_command_patterns and returns
    # `ask` (human confirms) or `deny`. On by default with `ask` -- low
    # friction (only the rare destructive command pauses). Set hitl_enabled
    # false to disable, or override the pattern list per project.
    "hitl_enabled": True,
    "dangerous_command_action": "ask",
    "dangerous_command_patterns": DEFAULT_DANGEROUS_COMMAND_PATTERNS,
}


def read_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if CFG_FILE.exists():
        try:
            cfg.update(json.loads(CFG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            # A malformed config silently falling back to defaults makes the
            # operator believe their overrides are active. Warn loudly (once
            # per hook invocation) on stderr and in the activity log.
            try:
                sys.stderr.write(
                    f"hooks: WARNING: {CFG_FILE} is malformed JSON "
                    f"({e.__class__.__name__}); using default config\n")
            except Exception:
                pass
            log_event(f"config: WARNING {CFG_FILE} is malformed JSON; using defaults")
    return cfg


# --------------------------------------------------------------------------- state


def _bootstrap_state() -> dict:
    # session_id = <unix-seconds><pid4>. pid suffix is the collision
    # guard: two Cursor windows bootstrapping in the same second get
    # distinct ids. All digits -> memory CORRELATION_RE (^\d+-\d+$)
    # stays happy.
    session_id = f"{int(time.time())}{os.getpid() % 10000:04d}"
    return {
        "session_id": session_id,
        "task_seq": 0,
        "active_correlation_id": f"{session_id}-0",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "writes": [],
        "subagent_calls": [],
        "reviewers_seen": [],
        "memory_updates": [],
        "enforcement_attempts": 0,
        "protocol_skipped": False,
    }


def _resolve_state_path() -> Path:
    env = os.environ.get("AGENT_PACK_HOOKS_STATE")
    return Path(env) if env else STATE_FILE


def load_state() -> dict:
    path = _resolve_state_path()
    if not path.exists():
        state = _bootstrap_state()
        save_state(state)
        return state
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        state = _bootstrap_state()
        save_state(state)
        return state


def save_state(state: dict) -> None:
    path = _resolve_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                      encoding="utf-8")
    json.dump(state, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, path)


def reset_state() -> dict:
    path = _resolve_state_path()
    if path.exists():
        path.unlink()
    return load_state()


# --------------------------------------------------------------------------- locking
#
# Cursor fires hooks for parallel tool calls concurrently, and every
# state-mutating phase does a read-modify-write on session.json. Without
# serialization, two concurrent processes can both read the same state and the
# last writer wins -- silently dropping a recorded write (the stop gate then
# under-counts and can fail OPEN) or letting two subagentStart calls both clear
# the concurrency cap. We take an OS advisory lock on a sidecar <state>.lock so
# the read-modify-write is atomic across processes.
#
# Acquisition is NON-BLOCKING with a bounded retry (~10s total) on every
# platform: a blocking LOCK_EX could hang a hook forever behind a crashed or
# slow peer, which freezes Cursor's whole tool-call pipeline. On timeout we
# log a warning and proceed UNLOCKED (best-effort: single-process behaviour is
# unchanged; worst case we are back to last-writer-wins for that one phase).
# os.replace alone gives an atomic file *swap*, not an atomic transaction
# across the read.
#
# IMPORTANT for phase authors: never run subprocesses while holding this lock.
# Acquire -> read/copy state -> release -> do slow work -> re-acquire ->
# mutate/save -> release. phase_session_start and phase_stop follow this
# pattern; the quick recorder phases hold the lock for their whole (fast)
# read-modify-write.

_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_SLEEP = 0.05


def _acquire_state_lock():
    path = _resolve_state_path()
    lock_path = path.parent / (path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+", encoding="utf-8")
    except Exception:
        return None
    acquired = False
    deadline = time.time() + _LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.time() >= deadline:
                        break
                    time.sleep(_LOCK_RETRY_SLEEP)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.time() >= deadline:
                        break
                    time.sleep(_LOCK_RETRY_SLEEP)
    except Exception:
        # Locking primitive unavailable (exotic FS / platform): proceed
        # unlocked, same as before.
        return fh
    if not acquired:
        log_event(
            f"state lock: TIMEOUT after {_LOCK_TIMEOUT_SECONDS:.0f}s waiting for "
            f"{lock_path.name}; proceeding UNLOCKED (concurrent update may be lost)"
        )
    return fh


def _release_state_lock(fh) -> None:
    if fh is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        try:
            fh.close()
        except Exception:
            pass


# Cap activity.log growth: once it exceeds _LOG_MAX_BYTES, keep only the most
# recent half (trimmed to a line boundary). Long-lived projects otherwise
# accumulate an unbounded log that slows every append and bloats backups.
_LOG_MAX_BYTES = 1_048_576  # 1 MiB


def _rotate_log_if_needed() -> None:
    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size <= _LOG_MAX_BYTES:
            return
        data = LOG_FILE.read_bytes()
        keep = data[len(data) // 2:]
        nl = keep.find(b"\n")
        if nl != -1:
            keep = keep[nl + 1:]
        tmp = tempfile.NamedTemporaryFile("wb", delete=False, dir=str(LOG_FILE.parent))
        tmp.write(b"[log rotated: older half discarded]\n" + keep)
        tmp.close()
        os.replace(tmp.name, LOG_FILE)
    except Exception:
        pass


def log_event(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- telemetry


def bump_telemetry(keypath: str, inc: int = 1, cfg: dict | None = None) -> None:
    if cfg is None:
        cfg = read_cfg()
    if not cfg.get("telemetry_enabled", True):
        return
    try:
        tel_dir = _resolve_telemetry_dir()
        tel_dir.mkdir(parents=True, exist_ok=True)
        tel_file = tel_dir / "agent-usage.json"
        try:
            data = json.loads(tel_file.read_text(encoding="utf-8")) if tel_file.exists() else {}
        except Exception:
            data = {}
        data.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        data.setdefault("agents", {})
        data.setdefault("summaries", {})
        parts = keypath.split(".")
        cur = data
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        last = parts[-1]
        if last == "last_at":
            cur[last] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        else:
            cur[last] = int(cur.get(last, 0)) + inc
        data["last_update_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(tel_dir),
                                          encoding="utf-8")
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.close()
        os.replace(tmp.name, tel_file)
    except Exception:
        pass


def bump_agent(slug: str) -> None:
    if not slug:
        return
    bump_telemetry(f"agents.{slug}.invocations")
    bump_telemetry(f"agents.{slug}.last_at")


# --------------------------------------------------------------------------- memory CLI discovery


def memory_cli_path() -> Path | None:
    env = os.environ.get("AGENT_PACK_MEMORY_CLI")
    if env and Path(env).exists():
        return Path(env)
    cur = HOOKS_DIR
    for _ in range(6):
        cand = cur / ".cursor" / "memory" / "memory.py"
        if cand.exists():
            return cand
        cur = cur.parent
    pack_mem = HOOKS_DIR.parent / "memory" / "memory.py"
    if pack_mem.exists():
        return pack_mem
    return None


def repomap_cli_path() -> "Path | None":
    """Locate the repo-map engine. Prefer the installed copy under the
    project's .cursor/repomap/, fall back to the pack's source script."""
    env = os.environ.get("AGENT_PACK_REPOMAP_CLI")
    if env and Path(env).exists():
        return Path(env)
    cur = HOOKS_DIR
    for _ in range(6):
        cand = cur / ".cursor" / "repomap" / "repomap.py"
        if cand.exists():
            return cand
        cur = cur.parent
    pack_rm = HOOKS_DIR.parent / "agents" / "scripts" / "repomap.py"
    if pack_rm.exists():
        return pack_rm
    return None


def _discover_pack_dir() -> "Path | None":
    """Locate the pack checkout by SIGNATURE (a directory containing
    agents/scripts/project_context.py) instead of hardcoding the default
    clone name `harmonist` -- users vendor the pack under any name.

    Search order: the hooks' own parent (pack-development mode / pack
    sources next to .cursor), then the project root itself, then each
    non-hidden immediate subdirectory of the project root (CWD and the
    post-integration `<project>/.cursor/..` root)."""
    sig = ("agents", "scripts", "project_context.py")

    def _has_sig(d: Path) -> bool:
        try:
            return d.joinpath(*sig).exists()
        except OSError:
            return False

    roots: list[Path] = []
    try:
        roots.append(Path.cwd())
    except OSError:
        pass
    roots.append(HOOKS_DIR.parent.parent)  # <project root> post-integration
    seen: set = set()
    for root in [HOOKS_DIR.parent] + roots:
        try:
            rroot = root.resolve()
        except OSError:
            continue
        if rroot in seen or not rroot.is_dir():
            continue
        seen.add(rroot)
        if _has_sig(rroot):
            return rroot
    for root in roots:
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            if _has_sig(child):
                return child
    return None


def _pack_hint() -> str:
    """Pack-dir name for human guidance strings. Generic placeholder when
    no checkout is discoverable (messages must not hardcode `harmonist`)."""
    d = _discover_pack_dir()
    return d.name if d is not None else "<pack-dir>"


def _strip_dot_slash(s: str) -> str:
    """Remove a leading './' prefix (repeated) WITHOUT stripping leading
    dots/slashes from real names. str.lstrip('./') would mangle '.github/...'
    into 'github/...' and '.eslintrc' into 'eslintrc' -- corrupting repo-map
    keys so the affected-tests lookup returns nothing (fail-open)."""
    while s.startswith("./"):
        s = s[2:]
    return s


def _relativize(paths: list[str], root: Path) -> list[str]:
    """Best-effort: express each path relative to `root` (POSIX), so they
    match the repo map's project-relative keys."""
    out: list[str] = []
    for p in paths:
        if not p:
            continue
        try:
            pp = Path(p)
            if pp.is_absolute():
                out.append(pp.resolve().relative_to(root.resolve()).as_posix())
            else:
                out.append(_strip_dot_slash(pp.as_posix()))
        except Exception:
            out.append(_strip_dot_slash(p.replace("\\", "/")))
    return out


def affected_tests_for(paths: list[str], project_root: Path) -> "list[str] | None":
    """Return the test files affected by `paths` via the repo map, or None
    if the map is unavailable. Best-effort, short timeout."""
    cli = repomap_cli_path()
    if cli is None or not paths:
        return None
    rel = _relativize(paths, project_root)
    try:
        r = subprocess.run(
            [sys.executable, str(cli), "affected", *rel,
             "--project", str(project_root), "--json"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    if r.returncode not in (0, 1):  # 1 = "(none)" which is valid/empty
        return None
    try:
        data = json.loads(r.stdout or "[]")
        return data if isinstance(data, list) else None
    except Exception:
        return None


# --------------------------------------------------------------------------- hook response helpers


def emit(response: dict) -> int:
    sys.stdout.write(json.dumps(response))
    sys.stdout.write("\n")
    return 0


def emit_allow() -> int:
    return emit({})


def emit_followup(message: str) -> int:
    return emit({"followup_message": message})


def emit_additional_context(message: str) -> int:
    return emit({"additional_context": message})


def emit_deny(user_message: str, agent_message: str = "") -> int:
    """Block the pending action (e.g. a subagent launch). Per Cursor's hook
    contract, subagentStart / beforeShellExecution honour
    {"permission": "deny"}."""
    resp = {"permission": "deny", "user_message": user_message}
    if agent_message:
        resp["agent_message"] = agent_message
    return emit(resp)


def _iso_to_epoch(ts: str) -> "float | None":
    """Parse a UTC '%Y-%m-%dT%H:%M:%SZ' timestamp to epoch seconds."""
    if not ts:
        return None
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None


def count_active_subagents(state: dict, cfg: dict) -> int:
    """Number of subagents that are currently OPEN (started, not stopped)
    and not older than the stale threshold. Stale entries are ignored so a
    missed subagentStop can't permanently inflate the count."""
    stale = int(cfg.get("subagent_stale_seconds", 900) or 0)
    now = time.time()
    n = 0
    for call in state.get("subagent_calls", []):
        if call.get("stopped_at") or call.get("completed"):
            continue
        if stale > 0:
            started = _iso_to_epoch(call.get("started_at") or call.get("at") or "")
            if started is not None and (now - started) > stale:
                continue
        n += 1
    return n


# --------------------------------------------------------------------------- helpers shared by write + subagent hooks


def _is_skipped_path(path: str, cfg: dict) -> bool:
    for pat in cfg.get("skip_path_patterns", []):
        if re.search(pat, path):
            return True
    return False


def _normalize_slashes(p: str) -> str:
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_memory_update_path(path: str, cfg: dict) -> bool:
    """True only when `path` is one of the configured memory FILES in the
    memory DIRECTORY -- not merely any file that shares a basename.

    A bare basename match (the old behaviour) cut both ways: a write to
    `frontend/patterns.md` silently bypassed the review gate (classified as
    a memory update), and a write to any random `session-handoff.md` could
    satisfy the stop gate's handoff requirement. We accept a path only when
    the basename matches AND the file lives in the memory dir: the exact
    configured relative path (default `.cursor/memory/...`), an absolute
    path ending in it, or a file whose parent directory resolves to the
    discovered memory dir (the directory containing memory.py -- honours
    $AGENT_PACK_MEMORY_CLI and the post-integration layout)."""
    p = _normalize_slashes(path)
    base = p.split("/")[-1]
    mem_paths = [str(m) for m in cfg.get("memory_paths", [])]
    basenames = {_normalize_slashes(m).rstrip("/").split("/")[-1] for m in mem_paths}
    if base not in basenames:
        return False
    for m in mem_paths:
        mm = _normalize_slashes(m)
        if p == mm or p.endswith("/" + mm):
            return True
    try:
        parent = Path(path).resolve().parent
    except Exception:
        return False
    cli = memory_cli_path()
    if cli is not None:
        try:
            if parent == cli.resolve().parent:
                return True
        except Exception:
            pass
    for m in mem_paths:
        mp = Path(m)
        cand = mp if mp.is_absolute() else (Path.cwd() / mp)
        try:
            if parent == cand.resolve().parent:
                return True
        except Exception:
            continue
    return False


def _locate_handoff_file(memory_updates: list, cfg: dict) -> "Path | None":
    """Find session-handoff.md on disk. Recorded memory_updates win; when
    absent we fall back to the file next to the memory CLI, then to the
    configured memory path resolved against the project root. The fallback
    matters because the DOCUMENTED write path is the memory.py CLI -- a shell
    command that never fires afterFileEdit, so memory_updates is legitimately
    empty in a real session."""
    candidates: list[Path] = []
    for e in memory_updates:
        p = str(e.get("path", ""))
        if p.endswith("session-handoff.md"):
            candidates.append(Path(p))
    cli = memory_cli_path()
    if cli is not None:
        candidates.append(cli.parent / "session-handoff.md")
    for m in cfg.get("memory_paths", []):
        mp = Path(str(m))
        if mp.name == "session-handoff.md":
            candidates.append(mp if mp.is_absolute() else (Path.cwd() / mp))
    for c in candidates:
        try:
            if c.exists():
                return c
        except Exception:
            continue
    return None


def _handoff_has_cid(content: str, active_cid: str) -> bool:
    """Line-anchored correlation-id match. A bare substring check let
    `...-1` match inside `...-10` and pass the gate on the wrong task."""
    if not active_cid:
        return True
    return re.search(
        rf"^correlation_id:\s*{re.escape(active_cid)}\s*$",
        content, re.MULTILINE,
    ) is not None


def _extract_slug_from_prompt(prompt: str) -> str:
    """AGENT: <slug> marker lives on the first non-empty line, but we
    also accept <!-- AGENT: <slug> --> as a fallback so a future
    change in Cursor's prompt handling (e.g. strip leading metadata)
    doesn't blind the hook."""
    if not prompt:
        return ""
    # Primary: bare AGENT: <slug> on the first 5 lines.
    for line in prompt.splitlines()[:5]:
        m = re.match(r"^\s*AGENT:\s*([A-Za-z0-9_\-]+)", line)
        if m:
            return m.group(1).strip().lower()
    # Fallbacks -- try to find the same pattern inside an HTML comment
    # or an XML-style tag, anywhere in the first 1000 chars.
    head = prompt[:1000]
    m = re.search(r"<!--\s*AGENT:\s*([A-Za-z0-9_\-]+)\s*-->", head)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"<agent[^>]*>\s*([A-Za-z0-9_\-]+)\s*</agent>", head, re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    return ""


def _extract_prompt_text(input_json: dict) -> str:
    """Pull the delegation prompt from whichever field Cursor populated.
    Mirrors record-subagent-start.sh so the Python and POSIX paths credit the
    same reviewer and apply the delegation gate to the same text."""
    for key in ("prompt", "task", "description", "input", "message"):
        v = input_json.get(key)
        if isinstance(v, str) and v.strip():
            return v
    ti = input_json.get("tool_input")
    if isinstance(ti, dict):
        for key in ("prompt", "task", "description"):
            v = ti.get(key)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def _final_message_text(input_json: dict) -> str:
    """Best-effort extraction of the agent's FINAL message from the stop hook
    input. We deliberately do NOT scan the whole serialized input: it echoes
    the sessionStart seed and prior followup text, both of which literally
    contain the 'PROTOCOL-SKIP: <one-line reason>' template -- scanning the
    whole blob would let that template fail the gate OPEN."""
    parts: list[str] = []
    for k in ("response", "final_message", "assistant_message", "message",
              "text", "content", "output", "last_message"):
        v = input_json.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    return "\n".join(parts)


def _detect_protocol_skip(input_json: dict) -> "str | None":
    text = _final_message_text(input_json)
    if not text:
        return None
    for m in re.finditer(r"PROTOCOL-SKIP:\s*([^\n\r\"]+)", text):
        reason = m.group(1).strip()
        # Ignore the instruction template echoed back verbatim
        # ('PROTOCOL-SKIP: <one-line reason>').
        if reason.startswith("<"):
            continue
        return reason
    return None


def _agent_is_readonly(slug: str) -> bool:
    """Targeted lookup of one agent's `readonly` frontmatter flag.

    Called once per subagentStart (rare) instead of loading the whole
    catalog on every file edit. Candidate locations: the project's
    `.cursor/agents/` relative to CWD, and `.cursor/agents/` next to the
    hooks dir (HOOKS_DIR.parent is `.cursor` post-integration -- the old
    second candidate resolved to the dead path `.cursor/.cursor/agents`).
    In pack-development mode the latter resolves to the pack's `agents/`
    source tree, which carries the same frontmatter."""
    if not slug:
        return False
    cand_dirs = [
        Path.cwd() / ".cursor" / "agents",
        HOOKS_DIR.parent / "agents",
    ]
    for d in cand_dirs:
        if not d.exists():
            continue
        agent_file = d / f"{slug}.md"
        if not agent_file.exists():
            agent_file = next(iter(d.rglob(f"{slug}.md")), None)
        if agent_file is None:
            continue
        try:
            text = agent_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not m:
            continue
        for line in m.group(1).splitlines():
            if re.match(r"^readonly:\s*(true|True|yes)\s*$", line):
                return True
        return False
    return False


# --------------------------------------------------------------------------- phases


def phase_session_start(input_json: dict) -> int:
    cfg = read_cfg()

    # --- Locked section: incidents + state reset. Everything below that
    # runs subprocesses (repomap status, memory.py latest, project_context)
    # happens AFTER the lock is released -- holding it through subprocess
    # timeouts (up to ~45s combined) would stall every concurrent hook.
    lock = _acquire_state_lock()
    try:
        # Pre-reset: read incidents.json for the protocol-exhausted banner.
        incidents_banner = ""
        try:
            if INCIDENTS_FILE.exists():
                data = json.loads(INCIDENTS_FILE.read_text(encoding="utf-8"))
                incidents = data.get("incidents") or []
                unsurfaced = [i for i in incidents if not i.get("surfaced_at")]
                if unsurfaced:
                    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    lines = [
                        "",
                        "!!  PROTOCOL-EXHAUSTED incident(s) from previous session(s):",
                    ]
                    for inc in unsurfaced[-3:]:
                        cid = inc.get("correlation_id", "?")
                        missing = inc.get("missing", [])[:2]
                        writes = inc.get("writes", [])[:3]
                        lines.append(f"    - cid={cid}  writes={writes}  missing={missing}")
                    lines.append(
                        "    These tasks were force-closed after the gate retried\n"
                        "    loop_limit times without protocol satisfaction. Writes may\n"
                        "    be in an inconsistent state (no reviewer, no handoff entry).\n"
                        "    Investigate the listed correlation_id(s) before starting new\n"
                        "    code changes. Full log: .cursor/hooks/.state/activity.log\n"
                    )
                    incidents_banner = "\n".join(lines)
                    for inc in incidents:
                        inc.setdefault("surfaced_at", now)
                    tmp = tempfile.NamedTemporaryFile("w", delete=False,
                                                      dir=str(INCIDENTS_FILE.parent),
                                                      encoding="utf-8")
                    json.dump(data, tmp, indent=2)
                    tmp.close()
                    os.replace(tmp.name, INCIDENTS_FILE)
        except Exception:
            incidents_banner = ""

        # Reset session state.
        state = reset_state()
        active_cid = state.get("active_correlation_id", "unknown")
    finally:
        _release_state_lock(lock)

    log_event("sessionStart: state reset")
    bump_telemetry("summaries.sessions", cfg=cfg)

    # PROTOCOL-SKIP abuse detection.
    skip_warning = ""
    if cfg.get("protocol_skip_warn_enabled", True) and TELEMETRY_FILE.exists():
        try:
            tel = json.loads(TELEMETRY_FILE.read_text(encoding="utf-8"))
            s = tel.get("summaries") or {}
            skips = int(s.get("protocol_skips", 0))
            sat = int(s.get("gate_allow_satisfied", 0))
            total = skips + sat
            min_count = int(cfg.get("protocol_skip_warn_threshold_count", 5))
            min_ratio = float(cfg.get("protocol_skip_warn_threshold_ratio", 0.25))
            if skips >= min_count and total > 0 and skips / total >= min_ratio:
                pct = int(round(skips / total * 100))
                skip_warning = (
                    f"\n!!  PROTOCOL-SKIP audit: {skips} of the last {total} "
                    f"completions used PROTOCOL-SKIP ({pct}%).\n"
                    "    The marker is for genuinely trivial turns (typo / comment).\n"
                    "    If you are using it to bypass qa-verifier on real code\n"
                    "    changes, STOP and run the full gate. Abuse is visible in\n"
                    "    .cursor/hooks/.state/activity.log.\n"
                )
        except Exception:
            pass

    # Repo-map staleness banner (informational; never blocks).
    repomap_banner = ""
    if cfg.get("repomap_staleness_warn", True):
        rm = repomap_cli_path()
        if rm is not None:
            try:
                r = subprocess.run(
                    [sys.executable, str(rm), "status", "--project", str(Path.cwd()), "--json"],
                    capture_output=True, text=True, timeout=15,
                )
                data = json.loads(r.stdout or "{}")
                if not data.get("built"):
                    repomap_banner = (
                        "\ni  Repo map not built yet. repo-scout is much cheaper "
                        "with it:\n   python3 .cursor/repomap/repomap.py build\n")
                elif int(data.get("pending", 0)) > 0:
                    repomap_banner = (
                        f"\ni  Repo map is stale: {data['pending']} file(s) changed "
                        "since the last index. Refresh before scouting:\n"
                        "   python3 .cursor/repomap/repomap.py refresh\n")
            except Exception:
                repomap_banner = ""

    # Latest memory entries.
    latest_state = ""
    latest_decisions = ""
    cli = memory_cli_path()
    if cli:
        try:
            r = subprocess.run(
                [sys.executable, str(cli), "latest", "--file", "session-handoff",
                 "--kind", "state", "--n", "3"],
                capture_output=True, text=True, timeout=10,
            )
            latest_state = r.stdout
        except Exception:
            pass
        try:
            r = subprocess.run(
                [sys.executable, str(cli), "latest", "--file", "decisions",
                 "--kind", "decision", "--n", "3"],
                capture_output=True, text=True, timeout=10,
            )
            latest_decisions = r.stdout
        except Exception:
            pass

    # Project context preamble. The pack dir is discovered by signature
    # (not the hardcoded clone name `harmonist`); see _discover_pack_dir.
    project_context = ""
    pack_dir = _discover_pack_dir()
    pack_hint = pack_dir.name if pack_dir is not None else "<pack-dir>"
    pc_candidates = [
        HOOKS_DIR.parent / "agents" / "scripts" / "project_context.py",
    ]
    if pack_dir is not None:
        pc_candidates.append(pack_dir / "agents" / "scripts" / "project_context.py")
    for cand in pc_candidates:
        if cand.exists():
            try:
                r = subprocess.run(
                    [sys.executable, str(cand), "--max-chars", "1200"],
                    capture_output=True, text=True, timeout=10,
                )
                project_context = r.stdout
            except Exception:
                pass
            break

    msg = (
        f"harmonist enforcement hooks are active in this session.\n"
        f"{skip_warning}{incidents_banner}{repomap_banner}\n"
        f"Active correlation_id for this task: {active_cid}\n\n"
        "Mandatory protocol reminders:\n"
        "1. Project AGENTS.md OVERRIDES any persona agent advice. When a persona\n"
        "   suggests an approach that conflicts with Invariants / Platform Stack\n"
        "   / Modules below, follow the project and flag the conflict in your\n"
        "   response.\n"
        "2. Delegate subagent work via Task; the FIRST line of every subagent\n"
        "   prompt MUST be 'AGENT: <slug>' (e.g. 'AGENT: qa-verifier'), and the\n"
        "   prompt MUST include a project-precedence preamble (use\n"
        f"   'python3 {pack_hint}/agents/scripts/project_context.py' to\n"
        "   generate it).\n"
        "3. After any code change: invoke qa-verifier (required) and any further\n"
        "   reviewers the trigger table in AGENTS.md demands.\n"
        "4. Append a session-handoff entry AT THE END OF EVERY TASK via the CLI:\n"
        "     python3 .cursor/memory/memory.py append \\\n"
        "       --file session-handoff --kind state --status done \\\n"
        "       --summary '<one line>' --body '<what changed / state / open issues>'\n"
        f"   The stop hook will block completion until an entry with\n"
        f"   correlation_id={active_cid} lands in session-handoff.md.\n"
        "5. For significant architectural choices, also append a decision entry\n"
        "   (kind: decision, file: decisions).\n\n"
        f"{project_context or '(project_context.py could not locate AGENTS.md; working without invariants injection)'}\n\n"
        "Recent state (last 3 entries from session-handoff.md):\n"
        f"{latest_state or '  (empty — first session or memory not yet seeded)'}\n\n"
        "Recent decisions (last 3 entries from decisions.md):\n"
        f"{latest_decisions or '  (empty)'}"
    )
    return emit_additional_context(msg)


def phase_after_file_edit(input_json: dict) -> int:
    cfg = read_cfg()
    state = load_state()
    path = str(input_json.get("file_path") or input_json.get("path") or "")
    if not path:
        return emit_allow()

    # Memory-file writes are tracked separately and BEFORE the skip check
    # (mirrors record-write.sh). `.cursor/` is in skip_path_patterns, so a
    # relative handoff path like `.cursor/memory/session-handoff.md` would
    # otherwise be skipped here and could never satisfy the stop gate's
    # session-handoff requirement -- the gate would loop to exhaustion on
    # every code task on the active (Python) path.
    #
    # Classification requires the file to actually live in the memory dir:
    # a bare basename match would let `frontend/patterns.md` bypass the
    # review gate entirely.
    if _is_memory_update_path(path, cfg):
        state.setdefault("memory_updates", []).append({
            "path": path,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        save_state(state)
        return emit_allow()

    if _is_skipped_path(path, cfg):
        return emit_allow()

    # Capability scoping: a readonly subagent writing files is a violation.
    # The readonly flag is resolved ONCE at subagentStart (targeted file
    # lookup) and tracked in active_readonly_subagents -- mirrors the .sh
    # path and avoids scanning the agent catalog on every routine edit.
    actives = state.get("active_readonly_subagents") or []
    if actives:
        state.setdefault("readonly_violations", []).append({
            "path": path,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "violator_slugs": sorted(set(actives)),
        })

    state.setdefault("writes", []).append({
        "path": path,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    save_state(state)
    return emit_allow()


def phase_subagent_start(input_json: dict) -> int:
    state = load_state()
    cfg = read_cfg()

    # Mechanical concurrency cap: refuse to launch another subagent while
    # `max_concurrent_subagents` are already running. This turns the
    # advisory "Max N concurrent" rule into a real gate and is the primary
    # guard against unbounded fan-out exhausting RAM.
    cap = int(cfg.get("max_concurrent_subagents", 3) or 0)
    if cap > 0:
        active = count_active_subagents(state, cfg)
        if active >= cap:
            bump_telemetry("summaries.subagent_cap_denials", cfg=cfg)
            log_event(f"subagentStart DENIED: {active} active >= cap {cap}")
            msg = (
                f"Concurrent-subagent limit reached: {active} subagent(s) are "
                f"already running (max_concurrent_subagents={cap}). Running too "
                "many subagents in parallel can exhaust memory, especially on a "
                "1M-context model. Wait for an active subagent to finish, then "
                "dispatch the next one (run them sequentially / in smaller "
                "batches). To allow more, raise max_concurrent_subagents in "
                ".cursor/hooks/config.json."
            )
            return emit_deny(msg, agent_message=msg)

    prompt = _extract_prompt_text(input_json)
    slug = _extract_slug_from_prompt(prompt)

    # Delegation-context gate (opt-in): a subagent only sees the text you hand
    # it -- not your conversation. A marker-only / near-empty delegation makes
    # the subagent guess and redo work. When require_delegation_context is on,
    # deny a delegation whose handoff (prompt minus the AGENT: marker) is
    # thinner than min_delegation_chars.
    if slug and cfg.get("require_delegation_context", False):
        body_text = re.sub(r"(?im)^\s*AGENT:\s*\S+\s*$", "", prompt)
        body_text = re.sub(r"<!--\s*AGENT:[^>]*-->", "", body_text)
        body_text = re.sub(r"<agent[^>]*>.*?</agent>", "", body_text, flags=re.IGNORECASE | re.DOTALL)
        min_chars = int(cfg.get("min_delegation_chars", 80) or 0)
        if len(body_text.strip()) < min_chars:
            bump_telemetry("summaries.delegation_context_denials", cfg=cfg)
            log_event(f"subagentStart DENIED: thin delegation to '{slug}' "
                      f"({len(body_text.strip())} < {min_chars} chars)")
            msg = (
                f"Thin delegation to '{slug}'. A subagent does NOT see your "
                "conversation — only the prompt you pass. Include the handoff "
                "package: the target/scope, the single sub-goal, constraints "
                "(authorization boundary, what NOT to do), and the success "
                "criteria, plus the PROJECT PRECEDENCE preamble. Re-dispatch "
                "with that context. (Disable via require_delegation_context in "
                ".cursor/hooks/config.json.)"
            )
            return emit_deny(msg, agent_message=msg)

    if not slug:
        # No marker -- still record the call, but it cannot satisfy the
        # reviewer gate.
        state.setdefault("subagent_calls", []).append({
            "slug": "",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        save_state(state)
        log_event("subagentStart: no AGENT: marker found")
        return emit_allow()
    # Capability scoping: resolve the agent's readonly flag here (one
    # targeted lookup per launch) so afterFileEdit can flag writes while a
    # readonly subagent is open without scanning the catalog per edit.
    readonly_flag = _agent_is_readonly(slug)
    state.setdefault("subagent_calls", []).append({
        "slug": slug,
        "readonly": readonly_flag,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    if readonly_flag:
        actives = state.setdefault("active_readonly_subagents", [])
        if slug not in actives:
            actives.append(slug)
    save_state(state)
    bump_agent(slug)
    log_event(f"subagentStart slug={slug}")
    return emit_allow()


def phase_subagent_stop(input_json: dict) -> int:
    state = load_state()
    cfg = read_cfg()
    # We don't know which call stopped; close the most-recently-started open
    # call (LIFO), matching record-subagent-stop.sh so the Python and POSIX
    # paths credit the same slug when subagents overlap. Mark BOTH stopped_at
    # and completed so the cap counter and gate agree no matter which path
    # wrote the entry.
    calls = state.get("subagent_calls", [])
    for call in reversed(calls):
        if call.get("stopped_at") or call.get("completed"):
            continue
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        call["stopped_at"] = now
        call["completed"] = True
        slug = (call.get("slug") or "").lower()
        if slug and slug in set(cfg.get("reviewer_slugs", [])):
            seen = set(state.get("reviewers_seen", []))
            seen.add(slug)
            state["reviewers_seen"] = sorted(seen)
        break
    # Capability scoping: reconcile active_readonly_subagents against the
    # OPEN call records -- a slug stays active only while at least one of
    # its invocations is still open. This both releases the slug we just
    # closed AND clears any stragglers when the stop matched no open
    # record (e.g. state from an older pack whose task bump wiped call
    # records): otherwise the readonly flag sticks forever and every
    # later edit records an un-remediable violation.
    open_slugs = {
        (c.get("slug") or "").lower()
        for c in calls
        if not (c.get("stopped_at") or c.get("completed"))
    }
    actives = state.get("active_readonly_subagents") or []
    pruned = [s for s in actives if s in open_slugs]
    if pruned != actives:
        state["active_readonly_subagents"] = pruned
    save_state(state)
    return emit_allow()


def _bump_task(state: dict, consumed: "dict | None" = None) -> None:
    """Advance to the next task. Clears per-task buckets, with two
    deliberate survivors:

    * OPEN subagent calls. A still-running background readonly reviewer
      (bg-regression-runner is readonly + is_background -- the documented
      happy path) must keep its call record across the bump: otherwise its
      late subagentStop finds no open record to close, its slug never
      leaves active_readonly_subagents, and every later edit is flagged as
      a readonly violation -- an un-remediable missing item that would
      force-exhaust every subsequent task.
    * Recorder events that landed AFTER the stop verdict's snapshot.
      `consumed` carries how many writes / memory_updates /
      readonly_violations the verdict actually consumed; anything beyond
      that arrived in the snapshot->bump window, belongs to the NEXT
      task, and is preserved instead of wiped. None = consume everything
      (bootstrap semantics)."""
    consumed = consumed or {}
    sid = state.get("session_id") or f"{int(time.time())}{os.getpid() % 10000:04d}"
    tseq = int(state.get("task_seq", 0)) + 1
    state["session_id"] = sid
    state["task_seq"] = tseq
    state["active_correlation_id"] = f"{sid}-{tseq}"

    def _tail(key: str) -> list:
        items = list(state.get(key, []))
        n = consumed.get(key)
        return [] if n is None else items[n:]

    state["writes"] = _tail("writes")
    state["memory_updates"] = _tail("memory_updates")
    state["readonly_violations"] = _tail("readonly_violations")
    state["subagent_calls"] = [
        c for c in state.get("subagent_calls", [])
        if not (c.get("stopped_at") or c.get("completed"))
    ]
    state["reviewers_seen"] = []
    state["enforcement_attempts"] = 0
    state["protocol_skipped"] = False
    state.pop("protocol_skip_reason", None)
    state["last_regression_ok"] = False


def _persist_incident(state: dict, final_missing: list[str]) -> None:
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "correlation_id": state.get("active_correlation_id", ""),
        "writes": [w.get("path", "?") for w in state.get("writes", [])][:10],
        "missing": list(final_missing),
        "reviewers_seen": list(state.get("reviewers_seen", [])),
        "attempts": state["enforcement_attempts"],
    }
    state.setdefault("protocol_incidents", []).append(entry)
    if len(state["protocol_incidents"]) > 20:
        state["protocol_incidents"] = state["protocol_incidents"][-20:]
    try:
        if INCIDENTS_FILE.exists():
            persisted = json.loads(INCIDENTS_FILE.read_text(encoding="utf-8"))
        else:
            persisted = {"incidents": []}
        persisted.setdefault("incidents", []).append(entry)
        if len(persisted["incidents"]) > 50:
            persisted["incidents"] = persisted["incidents"][-50:]
        tmp = tempfile.NamedTemporaryFile("w", delete=False,
                                          dir=str(INCIDENTS_FILE.parent),
                                          encoding="utf-8")
        json.dump(persisted, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, INCIDENTS_FILE)
    except Exception:
        pass


def _locked_state_mutation(mutator) -> dict:
    """Acquire the state lock, reload FRESH state, apply `mutator`, save,
    release. phase_stop runs its slow checks (validator / repomap
    subprocesses) without the lock; mutating a fresh copy here keeps
    recorder updates that landed in between, PROVIDED the mutator itself
    preserves them -- _bump_task takes `consumed` counts from the verdict
    snapshot precisely so window arrivals survive the bump instead of
    being reloaded fresh and then wiped."""
    lock = _acquire_state_lock()
    try:
        st = load_state()
        mutator(st)
        save_state(st)
        return st
    finally:
        _release_state_lock(lock)


def phase_stop(input_json: dict) -> int:
    cfg = read_cfg()

    # PROTOCOL-SKIP marker detection -- scoped to the agent's final-message
    # fields only (see _detect_protocol_skip; scanning the whole input would
    # match the echoed seed/followup template and fail OPEN).
    skip_reason = _detect_protocol_skip(input_json)

    # --- Locked: load state, record the skip flag, snapshot decision inputs.
    # Released BEFORE the slow section below (handoff file read, validator
    # subprocess up to 30s, repomap subprocess up to 20s).
    lock = _acquire_state_lock()
    try:
        state = load_state()
        if skip_reason:
            state["protocol_skipped"] = True
            state["protocol_skip_reason"] = skip_reason
            save_state(state)
    finally:
        _release_state_lock(lock)

    writes = state.get("writes", [])
    reviewers_seen = set(state.get("reviewers_seen", []))
    memory_updates = state.get("memory_updates", [])
    skipped = bool(state.get("protocol_skipped", False))
    active_cid = state.get("active_correlation_id", "")
    readonly_violations = state.get("readonly_violations", [])
    last_regression_ok = bool(state.get("last_regression_ok", False))
    last_regression_at = state.get("last_regression_at", "")

    # What this verdict consumed. Recorder events beyond these counts land
    # in the snapshot->bump window and survive _bump_task for the next task.
    consumed_counts = {
        "writes": len(writes),
        "memory_updates": len(memory_updates),
        "readonly_violations": len(readonly_violations),
    }

    def allow(reason: str) -> int:
        log_event(f"stop: allow ({reason})")
        bump_telemetry({
            "protocol-satisfied":          "summaries.gate_allow_satisfied",
            "protocol-explicitly-skipped": "summaries.protocol_skips",
            "no-writes":                   "summaries.gate_allow_no_writes",
            "trivial-only":                "summaries.gate_allow_trivial",
        }.get(reason, "summaries.gate_allow_other"), cfg=cfg)
        if reason in ("protocol-satisfied", "protocol-explicitly-skipped", "trivial-only"):
            st = _locked_state_mutation(
                lambda fresh: _bump_task(fresh, consumed=consumed_counts))
            log_event(f"task_seq bumped; next active_correlation_id={st['active_correlation_id']}")
        return emit_allow()

    def followup(message: str) -> int:
        def _mut(st: dict) -> None:
            st["enforcement_attempts"] = int(st.get("enforcement_attempts", 0)) + 1
        st = _locked_state_mutation(_mut)
        log_event(f"stop: followup (attempt={st['enforcement_attempts']})")
        bump_telemetry("summaries.gate_followups", cfg=cfg)
        return emit_followup(message)

    def exhausted(final_missing: list[str]) -> int:
        attempts_seen = {"n": 0}

        def _mut(st: dict) -> None:
            st["enforcement_attempts"] = int(st.get("enforcement_attempts", 0)) + 1
            attempts_seen["n"] = st["enforcement_attempts"]
            st["last_task_status"] = "protocol-exhausted"
            st["last_exhausted_correlation_id"] = st.get("active_correlation_id", "")
            _persist_incident(st, final_missing)
            _bump_task(st, consumed=consumed_counts)

        st = _locked_state_mutation(_mut)
        attempts = attempts_seen["n"]
        log_event(
            f"stop: EXHAUSTED after {attempts} attempts; "
            f"cid={st.get('last_exhausted_correlation_id', '')}; missing={final_missing}"
        )
        bump_telemetry("summaries.gate_exhausted", cfg=cfg)
        msg = (
            f"Protocol enforcement EXHAUSTED after "
            f"{attempts} attempts.\n\n"
            "This task has been force-closed as PROTOCOL-VIOLATED. The state\n"
            "file records a protocol-exhausted incident the next session will\n"
            "surface to the user. Fix the missing steps before starting new\n"
            "code changes, or the violation ratio in telemetry will grow.\n\n"
            "Missing (final attempt):\n" + "\n".join(f"  - {m}" for m in final_missing)
        )
        return emit_followup(msg)

    # --- Decision -----------------------------------------------------------
    if not writes:
        return allow("no-writes")
    if skipped:
        return allow("protocol-explicitly-skipped")

    # Lightweight mode.
    if cfg.get("allow_trivial_without_review", True):
        trivial = [re.compile(p) for p in cfg.get("trivial_path_patterns", [])]
        if trivial:
            def is_trivial(path: str) -> bool:
                return any(rx.search(path) for rx in trivial)
            if all(is_trivial(w.get("path", "")) for w in writes):
                log_event(f"lightweight-mode: bypassing reviewer ({len(writes)} paths)")
                return allow("trivial-only")

    missing: list[str] = []

    if readonly_violations:
        slugs = sorted({s for v in readonly_violations for s in v.get("violator_slugs", [])})
        paths = [v.get("path", "?") for v in readonly_violations[:3]]
        missing.append(
            f"readonly subagent(s) {slugs} made {len(readonly_violations)} "
            f"write(s) (e.g. {paths}). Readonly agents must not mutate files; "
            "redo this change via the appropriate non-readonly specialist."
        )
        bump_telemetry("summaries.readonly_violations", cfg=cfg)

    if cfg.get("require_any_reviewer", True) and not reviewers_seen:
        missing.append("no reviewer was invoked")
    if cfg.get("require_qa_verifier", True):
        required = cfg.get("required_reviewer_slug", "qa-verifier")
        if required not in reviewers_seen:
            missing.append(f"required reviewer '{required}' was not invoked")

    if cfg.get("require_regression_passed", False) and not last_regression_ok:
        suffix = f" (last run at {last_regression_at})" if last_regression_at else ""
        missing.append(
            "real regression run has not passed this task. "
            f"Run: python3 {_pack_hint()}/agents/scripts/run_regression.py" + suffix
        )

    # Impact-aware gate: if this task edited code that the repo map says
    # affects test files, require a passing regression run before finishing.
    if cfg.get("require_affected_tests", False) and not last_regression_ok:
        edited = [w.get("path", "") for w in writes]
        affected = affected_tests_for(edited, Path.cwd())
        if affected:
            shown = ", ".join(affected[:6]) + (
                f" (+{len(affected) - 6} more)" if len(affected) > 6 else "")
            missing.append(
                f"changed files affect {len(affected)} test file(s) that have "
                f"not been verified this task: {shown}. Run them (or the "
                "bg-regression-runner) and confirm green. The repo map computed "
                "this blast radius: python3 .cursor/repomap/repomap.py affected "
                "<changed files>."
            )
            bump_telemetry("summaries.affected_tests_gated", cfg=cfg)

    if cfg.get("require_session_handoff_update", True):
        # The documented write path is the memory.py CLI -- a shell command
        # that never fires afterFileEdit -- so memory_updates may not record
        # the handoff even when it WAS updated. _locate_handoff_file falls
        # back to the file on disk (next to the memory CLI / at the
        # configured memory path) and we accept it when it contains an entry
        # for the active correlation id (line-anchored, so `...-1` cannot
        # match inside `...-10`).
        handoff_file = _locate_handoff_file(memory_updates, cfg)
        if handoff_file is None:
            missing.append("session-handoff.md was not updated this task "
                           "(no handoff file found on disk either)")
        else:
            try:
                content = handoff_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            if not _handoff_has_cid(content, active_cid):
                missing.append(
                    f"session-handoff.md has no entry with correlation_id={active_cid} "
                    f"(the current task). Use memory.py to append one."
                )

    # Validate memory files. Runs whenever the handoff requirement is being
    # enforced -- NOT only when an edit-tool write was recorded: the
    # documented write path is the memory.py CLI (no afterFileEdit), so an
    # empty memory_updates must not skip schema validation of the handoff
    # the disk-fallback just accepted.
    cli = memory_cli_path()
    if cli and (memory_updates or cfg.get("require_session_handoff_update", True)):
        validator = cli.with_name("validate.py")
        if validator.exists():
            try:
                r = subprocess.run(
                    [sys.executable, str(validator), "--strict", "--quiet"],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode != 0:
                    missing.append("memory files failed schema validation:\n" + r.stderr.strip())
            except subprocess.TimeoutExpired:
                # Fail CLOSED: a hung validator must not let the gate pass.
                missing.append("memory schema validation timed out (>30s); "
                               "treating as NOT validated.")
            except Exception as e:
                missing.append("memory schema validation could not run "
                               f"({e.__class__.__name__}); fix before finishing.")

    if not missing:
        return allow("protocol-satisfied")

    # Fail-closed at loop_limit.
    loop_limit = int(cfg.get("loop_limit", 3))
    if int(state.get("enforcement_attempts", 0)) + 1 >= loop_limit:
        return exhausted(missing)

    wrote = ", ".join(w["path"] for w in writes[:5])
    more = "" if len(writes) <= 5 else f" (+{len(writes) - 5} more)"
    seen = ", ".join(sorted(reviewers_seen)) or "(none)"
    issues = "\n".join(f"  - {m}" for m in missing)
    return followup(
        "Protocol enforcement: cannot finish yet. This task edited:\n"
        f"  {wrote}{more}\n\n"
        "Missing protocol steps:\n"
        f"{issues}\n\n"
        f"Reviewers seen so far: {seen}\n"
        f"Active correlation_id for this task: {active_cid}\n\n"
        "Action required:\n"
        "  1. Delegate to the missing reviewers via Task subagents. The FIRST\n"
        "     line of each subagent prompt MUST be 'AGENT: <slug>'.\n"
        "     Example: 'AGENT: qa-verifier'.\n"
        "  2. Append a state entry to .cursor/memory/session-handoff.md using\n"
        "     the CLI so the correlation_id matches automatically:\n"
        "       python3 .cursor/memory/memory.py append \\\n"
        "         --file session-handoff --kind state --status done \\\n"
        "         --summary '<what changed this task>' \\\n"
        "         --body '<recent changes / open issues / services>'\n"
        "  3. If you made a significant architectural choice, also append a\n"
        "     decision entry (kind: decision, file: decisions).\n"
        "  4. Then return your final answer.\n\n"
        "If the task is genuinely trivial (typo / comment tweak) and the full\n"
        "protocol would be theatre, include 'PROTOCOL-SKIP: <one-line reason>'\n"
        "in your final message."
    )


# --------------------------------------------------------------------------- entry


def phase_before_shell_execution(input_json: dict) -> int:
    """HITL gate: ask for human confirmation before a destructive command.
    Returns {"permission": "ask"|"deny"} on a match, else allows."""
    cfg = read_cfg()
    if not cfg.get("hitl_enabled", True):
        return emit({"permission": "allow"})
    cmd = str(input_json.get("command") or input_json.get("cmd") or "")
    if not cmd.strip():
        # No command text: either the host sent an empty payload or stdin
        # timed out (see _read_stdin_payload). We cannot evaluate the
        # command against the safety patterns, so allowing silently would
        # fail OPEN on exactly the events this gate exists for. Ask.
        log_event("beforeShellExecution: empty payload; cannot evaluate -> ask")
        msg = (
            "The command safety gate received no command text from the "
            "host (empty hook payload), so it could not be checked against "
            "the dangerous-command patterns. Confirm the command manually. "
            "(Disable this gate via hitl_enabled in .cursor/hooks/config.json.)"
        )
        return emit({"permission": "ask", "user_message": msg, "agent_message": msg})
    patterns = cfg.get("dangerous_command_patterns") or DEFAULT_DANGEROUS_COMMAND_PATTERNS
    for pat in patterns:
        try:
            if re.search(pat, cmd):
                action = cfg.get("dangerous_command_action", "ask")
                action = action if action in ("ask", "deny") else "ask"
                bump_telemetry("summaries.hitl_gated", cfg=cfg)
                log_event(f"beforeShellExecution {action}: matched {pat!r}")
                msg = (
                    "This command is destructive / high-risk and matched a "
                    "safety guard:\n"
                    f"  {cmd[:200]}\n"
                    "A human should confirm it is intended before it runs. "
                    "(Tune via dangerous_command_patterns / dangerous_command_action "
                    "/ hitl_enabled in .cursor/hooks/config.json.)"
                )
                return emit({"permission": action, "user_message": msg, "agent_message": msg})
        except re.error as e:
            # A user-supplied pattern that does not compile is a silent hole
            # in the safety net -- name it so the operator can fix config.json.
            log_event(f"beforeShellExecution: WARNING invalid dangerous_command_pattern "
                      f"{pat!r} skipped ({e})")
            continue
    return emit({"permission": "allow"})


PHASES = {
    "sessionStart":        phase_session_start,
    "afterFileEdit":       phase_after_file_edit,
    "subagentStart":       phase_subagent_start,
    "subagentStop":        phase_subagent_stop,
    "stop":                phase_stop,
    "beforeShellExecution": phase_before_shell_execution,
}


def _read_stdin_payload(timeout: float = 5.0) -> str:
    """Read the hook payload from stdin without risking an eternal hang.

    A host that launches the hook but never writes/closes stdin would block
    a bare sys.stdin.read() forever -- freezing Cursor's tool pipeline. On
    POSIX we wait up to `timeout` seconds for the first byte via select();
    if nothing arrives we proceed with an empty payload (every phase
    tolerates {}). Windows has no select() on pipes, so it keeps the plain
    read (the documented Cursor host behaviour closes stdin promptly)."""
    if os.name == "nt":
        return sys.stdin.read()
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            log_event(f"stdin: no payload within {timeout:.0f}s; proceeding with empty input")
            return ""
        return sys.stdin.read()
    except Exception:
        return sys.stdin.read()


# Phases that manage the state lock themselves (sessionStart / stop need to
# run subprocesses OUTSIDE the lock; beforeShellExecution never touches
# session.json). The quick recorder phases are wrapped by main().
_SELF_LOCKING_PHASES = {"sessionStart", "stop", "beforeShellExecution"}


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        sys.stderr.write(
            "hook_runner.py: phase argument required.\n"
            "Usage: python3 hook_runner.py <phase>\n"
            f"Phases: {', '.join(PHASES)}\n"
        )
        return 2
    phase = argv[0]
    if phase not in PHASES:
        sys.stderr.write(f"hook_runner.py: unknown phase {phase!r}. "
                         f"Known: {', '.join(PHASES)}\n")
        return 2
    raw = _read_stdin_payload()
    try:
        input_json = json.loads(raw) if raw.strip() else {}
    except Exception:
        input_json = {}

    fn = PHASES[phase]
    # Recorder phases (afterFileEdit / subagentStart / subagentStop) do one
    # fast read-modify-write that must be serialized; hold the lock around
    # them here. sessionStart and stop lock around their state sections
    # internally so their subprocess work runs unlocked.
    lock = _acquire_state_lock() if phase not in _SELF_LOCKING_PHASES else None
    try:
        return fn(input_json)
    except Exception as e:
        try:
            log_event(f"{phase}: INTERNAL ERROR {e.__class__.__name__}: {e}")
        except Exception:
            pass
        # The stop gate must FAIL CLOSED: an internal error must never let the
        # turn end silently with no JSON (which Cursor reads as "no
        # enforcement"). Emit a followup directly (no state writes -- state I/O
        # may be exactly what broke); Cursor's loop_limit still caps repeats.
        if phase == "stop":
            return emit({"followup_message": (
                "Protocol enforcement hit an internal error and cannot confirm "
                "this task satisfied the gate. Treating it as NOT satisfied "
                "(fail-closed). Re-run your final step; if this persists, "
                "inspect .cursor/hooks/.state/activity.log. "
                f"(internal: {e.__class__.__name__})"
            )})
        if phase == "beforeShellExecution":
            # A broken safety gate should ASK for confirmation, not silently run.
            return emit({"permission": "ask", "user_message": (
                "The command safety gate errored; confirm this command is "
                "safe before running it.")})
        # Recorder phases (sessionStart/afterFileEdit/subagent*): a failure
        # here must not wedge the agent -- allow, and rely on the stop gate.
        return emit_allow()
    finally:
        _release_state_lock(lock)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
