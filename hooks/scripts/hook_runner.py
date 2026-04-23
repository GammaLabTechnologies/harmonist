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
        r"(?i)docs/",
        r"(?i)documentation/",
    ],
    "protocol_skip_warn_enabled": True,
    "protocol_skip_warn_threshold_count": 5,
    "protocol_skip_warn_threshold_ratio": 0.25,
    "loop_limit": 3,
}


def read_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if CFG_FILE.exists():
        try:
            cfg.update(json.loads(CFG_FILE.read_text()))
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
        return json.loads(path.read_text())
    except Exception:
        state = _bootstrap_state()
        save_state(state)
        return state


def save_state(state: dict) -> None:
    path = _resolve_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent)
    json.dump(state, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, path)


def reset_state() -> dict:
    path = _resolve_state_path()
    if path.exists():
        path.unlink()
    return load_state()


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
            data = json.loads(tel_file.read_text()) if tel_file.exists() else {}
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
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(tel_dir))
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
                text = md.read_text()
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
            data = json.loads(INCIDENTS_FILE.read_text())
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
                tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(INCIDENTS_FILE.parent))
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
            tel = json.loads(TELEMETRY_FILE.read_text())
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
        f"{skip_warning}{incidents_banner}\n"
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
    if _is_skipped_path(path, cfg):
        return emit_allow()
    # Capability scoping: if any currently-active subagent is readonly,
    # this write is a violation.
    catalog = _load_agent_catalog()
    active_calls = state.get("subagent_calls", [])
    readonly_violators = []
    for call in active_calls:
        if call.get("stopped_at"):
            continue
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
    # Also record any memory-file write for the stop gate's handoff check.
    for memp in cfg.get("memory_paths", []):
        if path.endswith(memp) or path.endswith(memp.lstrip(".")):
            state.setdefault("memory_updates", []).append({"path": path})
            save_state(state)
            break
    return emit_allow()


def phase_subagent_start(input_json: dict) -> int:
    state = load_state()
    prompt = str(input_json.get("prompt") or "")
    slug = _extract_slug_from_prompt(prompt)
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
    # We don't know which call stopped; close the oldest open call.
    calls = state.get("subagent_calls", [])
    for call in calls:
        if not call.get("stopped_at"):
            call["stopped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            slug = (call.get("slug") or "").lower()
            if slug and slug in set(cfg.get("reviewer_slugs", [])):
                seen = set(state.get("reviewers_seen", []))
                seen.add(slug)
                state["reviewers_seen"] = sorted(seen)
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
            persisted = json.loads(INCIDENTS_FILE.read_text())
        else:
            persisted = {"incidents": []}
        persisted.setdefault("incidents", []).append(entry)
        if len(persisted["incidents"]) > 50:
            persisted["incidents"] = persisted["incidents"][-50:]
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(INCIDENTS_FILE.parent))
        json.dump(persisted, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, INCIDENTS_FILE)
    except Exception:
        pass


def phase_stop(input_json: dict) -> int:
    cfg = read_cfg()
    state = load_state()

    # PROTOCOL-SKIP marker detection.
    raw = json.dumps(input_json) if input_json else ""
    m = re.search(r"PROTOCOL-SKIP:\s*([^\n\r\"]+)", raw)
    if m:
        state["protocol_skipped"] = True
        state["protocol_skip_reason"] = m.group(1).strip()
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
                content = handoff_file.read_text()
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
            r = subprocess.run(
                [sys.executable, str(validator), "--strict", "--quiet"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                missing.append("memory files failed schema validation:\n" + r.stderr.strip())

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


PHASES = {
    "sessionStart":    phase_session_start,
    "afterFileEdit":   phase_after_file_edit,
    "subagentStart":   phase_subagent_start,
    "subagentStop":    phase_subagent_stop,
    "stop":            phase_stop,
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
    return PHASES[phase](input_json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
