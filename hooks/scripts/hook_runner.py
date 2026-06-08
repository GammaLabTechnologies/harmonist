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
    # --force /`, `rm -rf --no-preserve-root /`.
    r"(?:^|[\s;&|(])rm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*|--recursive|--force|--no-preserve-root)(?:\s+(?:-[a-zA-Z-]+|--[a-zA-Z-]+))*\s+(?:/(?:\s|$|/|\*|[a-zA-Z])|~|\$HOME|\*|\.(?:\s|$|/))",
    r"\bgit\s+push\b[^\n]*(?:--force(?!-with-lease)|\s-f\b)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    # dd reading or writing a raw device, regardless of if=/of= order:
    # `dd if=/dev/zero of=/dev/sda` AND `dd of=/dev/sda if=/dev/zero`.
    r"\bdd\s+[^\n]*\b(?:if|of)=/",
    r"\bmkfs[.\s]",
    r":\(\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:",
    r"\b(?:shutdown|reboot|halt|poweroff)\b",
    r"\bchmod\s+-R\s+0?777\b",
    r">\s*/dev/(?:sd|nvme|disk|hd)",
    r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b",
    r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b",
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
        except Exception:
            pass
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
# the concurrency cap. We take an OS advisory lock on a sidecar <state>.lock for
# the whole phase so the read-modify-write is atomic across processes.
# Best-effort: if locking is unavailable we proceed (single-process behaviour is
# unchanged). os.replace alone gives an atomic file *swap*, not an atomic
# transaction across the read.


def _acquire_state_lock():
    path = _resolve_state_path()
    lock_path = path.parent / (path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")
    except Exception:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            for _ in range(600):  # spin up to ~30s; normally instant
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass
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


def log_event(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with LOG_FILE.open("a") as fh:
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


def _load_agent_catalog() -> dict[str, dict]:
    """Best-effort lookup of installed .cursor/agents/*.md files for
    capability scoping (readonly flag etc.). Returns a dict keyed by
    slug (filename stem)."""
    cwd = Path.cwd()
    cand_dirs = [
        cwd / ".cursor" / "agents",
        HOOKS_DIR.parent / ".cursor" / "agents",
    ]
    catalog: dict[str, dict] = {}
    for d in cand_dirs:
        if not d.exists():
            continue
        for md in d.rglob("*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm = {}
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            if not m:
                continue
            for line in m.group(1).splitlines():
                mm = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$", line)
                if not mm:
                    continue
                key, val = mm.group(1), mm.group(2).strip()
                if val.lower() == "true":
                    fm[key] = True
                elif val.lower() == "false":
                    fm[key] = False
                else:
                    fm[key] = val.strip("'\"")
            catalog[md.stem] = fm
    return catalog


# --------------------------------------------------------------------------- phases


def phase_session_start(input_json: dict) -> int:
    cfg = read_cfg()

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

    # Project context preamble.
    project_context = ""
    pc_candidates = [
        HOOKS_DIR.parent / "agents" / "scripts" / "project_context.py",
        HOOKS_DIR.parent.parent / "harmonist" / "agents" / "scripts" / "project_context.py",
    ]
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

    active_cid = state.get("active_correlation_id", "unknown")
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
        "   'python3 harmonist/agents/scripts/project_context.py' to\n"
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
    mem_paths = cfg.get("memory_paths", [])
    is_memory = path in mem_paths or any(
        path.endswith(m.split("/")[-1]) for m in mem_paths
    )
    if is_memory:
        state.setdefault("memory_updates", []).append({
            "path": path,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        save_state(state)
        return emit_allow()

    if _is_skipped_path(path, cfg):
        return emit_allow()

    # Capability scoping: a readonly subagent writing files is a violation.
    # Only read the agent catalog when a subagent is actually open -- avoids
    # scanning ~150 agent files on every routine edit.
    active_calls = [
        c for c in state.get("subagent_calls", [])
        if not (c.get("stopped_at") or c.get("completed"))
    ]
    if active_calls:
        catalog = _load_agent_catalog()
        readonly_violators = []
        for call in active_calls:
            slug = (call.get("slug") or "").lower()
            info = catalog.get(slug) or {}
            if info.get("readonly") is True:
                readonly_violators.append(slug)
        if readonly_violators:
            state.setdefault("readonly_violations", []).append({
                "path": path,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "violator_slugs": sorted(set(readonly_violators)),
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
    state.setdefault("subagent_calls", []).append({
        "slug": slug,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
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
        # Capability scoping: drop the slug from the active-readonly list so
        # writes after this invocation aren't flagged (parity with the .sh
        # path, which tracks active_readonly_subagents).
        if slug:
            actives = state.get("active_readonly_subagents") or []
            if slug in actives:
                actives.remove(slug)
                state["active_readonly_subagents"] = actives
        break
    save_state(state)
    return emit_allow()


def _bump_task(state: dict) -> None:
    sid = state.get("session_id") or f"{int(time.time())}{os.getpid() % 10000:04d}"
    tseq = int(state.get("task_seq", 0)) + 1
    state["session_id"] = sid
    state["task_seq"] = tseq
    state["active_correlation_id"] = f"{sid}-{tseq}"
    state["writes"] = []
    state["subagent_calls"] = []
    state["reviewers_seen"] = []
    state["memory_updates"] = []
    state["enforcement_attempts"] = 0
    state["protocol_skipped"] = False
    state.pop("protocol_skip_reason", None)
    state["last_regression_ok"] = False
    state["readonly_violations"] = []


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


def phase_stop(input_json: dict) -> int:
    cfg = read_cfg()
    state = load_state()

    # PROTOCOL-SKIP marker detection -- scoped to the agent's final-message
    # fields only (see _detect_protocol_skip; scanning the whole input would
    # match the echoed seed/followup template and fail OPEN).
    skip_reason = _detect_protocol_skip(input_json)
    if skip_reason:
        state["protocol_skipped"] = True
        state["protocol_skip_reason"] = skip_reason
        save_state(state)

    writes = state.get("writes", [])
    reviewers_seen = set(state.get("reviewers_seen", []))
    memory_updates = state.get("memory_updates", [])
    skipped = bool(state.get("protocol_skipped", False))
    active_cid = state.get("active_correlation_id", "")
    readonly_violations = state.get("readonly_violations", [])
    last_regression_ok = bool(state.get("last_regression_ok", False))
    last_regression_at = state.get("last_regression_at", "")

    def allow(reason: str) -> int:
        log_event(f"stop: allow ({reason})")
        bump_telemetry({
            "protocol-satisfied":          "summaries.gate_allow_satisfied",
            "protocol-explicitly-skipped": "summaries.protocol_skips",
            "no-writes":                   "summaries.gate_allow_no_writes",
            "trivial-only":                "summaries.gate_allow_trivial",
        }.get(reason, "summaries.gate_allow_other"), cfg=cfg)
        if reason in ("protocol-satisfied", "protocol-explicitly-skipped", "trivial-only"):
            _bump_task(state)
            save_state(state)
            log_event(f"task_seq bumped; next active_correlation_id={state['active_correlation_id']}")
        return emit_allow()

    def followup(message: str) -> int:
        state["enforcement_attempts"] = int(state.get("enforcement_attempts", 0)) + 1
        save_state(state)
        log_event(f"stop: followup (attempt={state['enforcement_attempts']})")
        bump_telemetry("summaries.gate_followups", cfg=cfg)
        return emit_followup(message)

    def exhausted(final_missing: list[str]) -> int:
        state["enforcement_attempts"] = int(state.get("enforcement_attempts", 0)) + 1
        state["last_task_status"] = "protocol-exhausted"
        state["last_exhausted_correlation_id"] = state.get("active_correlation_id", "")
        _persist_incident(state, final_missing)
        log_event(
            f"stop: EXHAUSTED after {state['enforcement_attempts']} attempts; "
            f"cid={state.get('active_correlation_id','')}; missing={final_missing}"
        )
        bump_telemetry("summaries.gate_exhausted", cfg=cfg)
        _bump_task(state)
        save_state(state)
        msg = (
            f"Protocol enforcement EXHAUSTED after "
            f"{state['enforcement_attempts']} attempts.\n\n"
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
            "Run: python3 harmonist/agents/scripts/run_regression.py" + suffix
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
        handoff_paths = [
            e.get("path", "") for e in memory_updates
            if e.get("path", "").endswith("session-handoff.md")
        ]
        if not handoff_paths:
            missing.append("session-handoff.md was not updated this task")
        else:
            handoff_file = None
            for p in handoff_paths:
                fp = Path(p)
                if fp.exists():
                    handoff_file = fp
                    break
            if handoff_file is None:
                missing.append(f"session-handoff.md at {handoff_paths[0]} not readable")
            else:
                content = handoff_file.read_text(encoding="utf-8", errors="replace")
                if active_cid and f"correlation_id: {active_cid}" not in content:
                    missing.append(
                        f"session-handoff.md has no entry with correlation_id={active_cid} "
                        f"(the current task). Use memory.py to append one."
                    )

    # Validate memory files.
    cli = memory_cli_path()
    if cli and memory_updates:
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
        return emit({"permission": "allow"})
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
        except re.error:
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
    raw = sys.stdin.read()
    try:
        input_json = json.loads(raw) if raw.strip() else {}
    except Exception:
        input_json = {}

    fn = PHASES[phase]
    # beforeShellExecution doesn't touch session.json, so it needs no lock.
    # Every other phase does a read-modify-write that must be serialized.
    lock = _acquire_state_lock() if phase != "beforeShellExecution" else None
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
