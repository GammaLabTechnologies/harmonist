#!/usr/bin/env bash
# stop hook -- THE ENFORCER.
#
# When the agent tries to finish responding we check the session state:
#   * If no writes happened -> allow (pure Q&A, no protocol needed).
#   * If writes happened, we REQUIRE:
#       - A reviewer from CFG.reviewer_slugs was invoked.
#       - CFG.required_reviewer_slug (default: qa-verifier) was invoked.
#       - session-handoff.md was updated AND contains an entry whose
#         correlation_id matches the current active_correlation_id.
#       - All touched memory files pass the schema validator.
#
# On failure we return followup_message and increment an attempt counter.
# loop_limit in hooks.json caps retries.
# On a successful, protocol-satisfied stop we advance task_seq so the next
# task begins with a fresh correlation_id.

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin
state_init

# Record any PROTOCOL-SKIP marker from the agent's final message BEFORE the
# main python helper reads state.
state_update '
import re
blob = json.dumps(INPUT) if INPUT else ""
m = re.search(r"PROTOCOL-SKIP:\s*([^\n\r\"]+)", blob)
if m:
    STATE["protocol_skipped"] = True
    STATE["protocol_skip_reason"] = m.group(1).strip()
'

MEMORY_CLI="$(memory_cli_path)"

# Single python block: decide verdict, and if it's an allow with writes,
# also advance task_seq so the next task starts clean. Emit the final
# hook response JSON on stdout.
STATE_FILE_PATH="$STATE_FILE" CFG_JSON="$(read_cfg)" MEMORY_CLI="$MEMORY_CLI" \
python3 - <<'PY'
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

state_path = pathlib.Path(os.environ["STATE_FILE_PATH"])
state = json.loads(state_path.read_text())
cfg = json.loads(os.environ["CFG_JSON"])
memory_cli = os.environ.get("MEMORY_CLI", "")

writes = state.get("writes", [])
reviewers_seen = set(state.get("reviewers_seen", []))
memory_updates = state.get("memory_updates", [])
skipped = state.get("protocol_skipped", False)
active_cid = state.get("active_correlation_id", "")
readonly_violations = state.get("readonly_violations", [])
last_regression_ok = bool(state.get("last_regression_ok", False))
last_regression_at = state.get("last_regression_at", "")


def log(msg: str) -> None:
    log_file = state_path.with_name("activity.log")
    try:
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log_file.open("a") as fh:
            fh.write(f"[{ts}] stop: {msg}\n")
    except Exception:
        pass


def bump_task() -> None:
    """Advance task_seq and reset per-task buckets so the next task starts clean."""
    sid = state["session_id"]
    state["task_seq"] = int(state.get("task_seq", 0)) + 1
    state["active_correlation_id"] = f"{sid}-{state['task_seq']}"
    state["writes"] = []
    state["subagent_calls"] = []
    state["reviewers_seen"] = []
    state["memory_updates"] = []
    state["enforcement_attempts"] = 0
    state["protocol_skipped"] = False
    state.pop("protocol_skip_reason", None)
    # A prior task's regression pass does not carry over into the new
    # task (new writes -> new regression required if the gate is on).
    state["last_regression_ok"] = False
    state["readonly_violations"] = []


def save_state() -> None:
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=state_path.parent)
    json.dump(state, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, state_path)


def _telemetry_bump(key: str) -> None:
    """Increment a summaries.<key> counter in .cursor/telemetry/agent-usage.json.
    Fail silently -- telemetry must never break the hook."""
    if not cfg.get("telemetry_enabled", True):
        return
    try:
        override = os.environ.get("AGENT_PACK_TELEMETRY_DIR")
        if override:
            tel_dir = pathlib.Path(override)
        else:
            tel_dir = state_path.parent.parent.parent / "telemetry"
        tel_dir.mkdir(parents=True, exist_ok=True)
        tel_file = tel_dir / "agent-usage.json"
        import time as _t
        try:
            data = json.loads(tel_file.read_text()) if tel_file.exists() else {}
        except Exception:
            data = {}
        data.setdefault("started_at", _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()))
        data.setdefault("summaries", {})
        data["summaries"][key] = int(data["summaries"].get(key, 0)) + 1
        data["last_update_at"] = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        import tempfile as _tmp
        tmp = _tmp.NamedTemporaryFile("w", delete=False, dir=str(tel_dir))
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.close()
        os.replace(tmp.name, tel_file)
    except Exception:
        pass


def emit_allow(reason: str) -> None:
    log(f"allow ({reason})")
    _telemetry_bump({
        "protocol-satisfied":         "gate_allow_satisfied",
        "protocol-explicitly-skipped":"protocol_skips",
        "no-writes":                  "gate_allow_no_writes",
        "trivial-only":               "gate_allow_trivial",
    }.get(reason, "gate_allow_other"))
    if reason in ("protocol-satisfied", "protocol-explicitly-skipped",
                  "trivial-only"):
        bump_task()
        save_state()
        log(f"task_seq bumped; next active_correlation_id={state['active_correlation_id']}")
    print("{}")


def emit_followup(message: str) -> None:
    state["enforcement_attempts"] = int(state.get("enforcement_attempts", 0)) + 1
    save_state()
    log(f"followup (attempt={state['enforcement_attempts']})")
    _telemetry_bump("gate_followups")
    print(json.dumps({"followup_message": message}))


def emit_exhausted(final_missing: list[str]) -> None:
    """Fail-CLOSED when the hook's loop_limit is about to fire the last
    attempt. We do NOT silently allow: we mark the current task as
    protocol-violated in the state file, persist an incident record
    the next sessionStart will surface to the user, AND still emit a
    final followup so Cursor at least flags the attempt.

    Why this matters: a stubborn agent can "wait out" N followup
    attempts by repeating its final message. Without this branch,
    after loop_limit the gate emits '{}' (allow) and the state
    silently drifts -- writes done, reviewers not called, task_seq
    not bumped. Now the state carries `last_task_status:
    protocol-exhausted` + an incident log entry that the next
    session's seed-session hook surfaces in additional_context.
    """
    import time as _t
    state["enforcement_attempts"] = int(state.get("enforcement_attempts", 0)) + 1
    # Mark the task explicitly as protocol-violated. We DO bump
    # task_seq (so the user is not stuck forever on this task), but
    # we record the incident alongside the correlation_id that
    # failed.
    incidents = state.setdefault("protocol_incidents", [])
    incidents.append({
        "at": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        "correlation_id": state.get("active_correlation_id", ""),
        "writes": [w.get("path", "?") for w in state.get("writes", [])][:10],
        "missing": list(final_missing),
        "reviewers_seen": sorted(reviewers_seen),
        "attempts": state["enforcement_attempts"],
    })
    # Keep only the last 20 incidents in-state to bound growth; the
    # permanent record is in activity.log.
    if len(incidents) > 20:
        state["protocol_incidents"] = incidents[-20:]
    state["last_task_status"] = "protocol-exhausted"
    state["last_exhausted_correlation_id"] = state.get("active_correlation_id", "")
    # Persist to a separate incidents.json that survives session_reset()
    # -- otherwise the next sessionStart wipes state and the warning is
    # gone. The seed-session hook reads incidents.json to surface a
    # banner in additional_context.
    try:
        incidents_path = state_path.with_name("incidents.json")
        if incidents_path.exists():
            persisted = json.loads(incidents_path.read_text())
        else:
            persisted = {"incidents": []}
        persisted.setdefault("incidents", []).append(incidents[-1])
        # Cap persisted incidents at 50 to prevent unbounded growth.
        if len(persisted["incidents"]) > 50:
            persisted["incidents"] = persisted["incidents"][-50:]
        import tempfile as _tmp
        tmp = _tmp.NamedTemporaryFile("w", delete=False, dir=state_path.parent)
        json.dump(persisted, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, incidents_path)
    except Exception:
        pass  # incidents.json is best-effort; never break the hook
    log(
        f"EXHAUSTED after {state['enforcement_attempts']} attempts; "
        f"cid={state.get('active_correlation_id','')}; missing={final_missing}"
    )
    _telemetry_bump("gate_exhausted")
    # Advance task_seq so the next task isn't blocked by stale
    # reviewer buckets, but keep the incident record so the next
    # sessionStart can surface it.
    bump_task()
    save_state()
    # Still emit a followup as the final word -- Cursor's loop_limit
    # will show it to the user even if it refuses to retry.
    msg = (
        "Protocol enforcement EXHAUSTED after "
        f"{state['enforcement_attempts']} attempts.\n\n"
        "This task has been force-closed as PROTOCOL-VIOLATED. The state\n"
        "file records a protocol-exhausted incident the next session will\n"
        "surface to the user. Fix the missing steps before starting new\n"
        "code changes, or the violation ratio in telemetry will grow.\n\n"
        "Missing (final attempt):\n" + "\n".join(f"  - {m}" for m in final_missing)
    )
    print(json.dumps({"followup_message": msg}))


# --- Decision ---------------------------------------------------------------

if not writes:
    emit_allow("no-writes")
    sys.exit(0)

if skipped:
    emit_allow("protocol-explicitly-skipped")
    sys.exit(0)

# --- Lightweight-mode: trivial writes bypass the reviewer gate -------------
#
# When every write in the task targets a "trivial" path (docs, README,
# CHANGELOG, LICENSE, .gitignore, docs/...), the full protocol loop adds
# latency without catching real bugs. `allow_trivial_without_review` in
# config controls the behaviour (default: true). The check is strict
# "all or nothing" -- a single code-file write defeats the fast path,
# so there's no way to hide a production change inside a doc edit.
if cfg.get("allow_trivial_without_review", True):
    trivial_patterns = [
        re.compile(p) for p in cfg.get("trivial_path_patterns", [])
    ]
    if trivial_patterns:
        def _is_trivial_path(path: str) -> bool:
            for rx in trivial_patterns:
                if rx.search(path):
                    return True
            return False

        all_trivial = bool(writes) and all(
            _is_trivial_path(w.get("path", "")) for w in writes
        )
        if all_trivial:
            log(
                "lightweight-mode: every write hit a trivial path; "
                f"bypassing reviewer ({len(writes)} paths)"
            )
            emit_allow("trivial-only")
            sys.exit(0)

missing: list[str] = []

# Capability scoping: a readonly subagent writing files is a protocol
# violation regardless of later reviewer activity. The gate refuses
# unconditionally. The orchestrator is expected to redo the change via
# a non-readonly specialist.
if readonly_violations:
    slugs = sorted({
        s
        for v in readonly_violations
        for s in v.get("violator_slugs", [])
    })
    paths = [v.get("path", "?") for v in readonly_violations[:3]]
    missing.append(
        f"readonly subagent(s) {slugs} made {len(readonly_violations)} "
        f"write(s) (e.g. {paths}). Readonly agents must not mutate files; "
        "redo this change via the appropriate non-readonly specialist."
    )
    _telemetry_bump("readonly_violations")

if cfg["require_any_reviewer"] and not reviewers_seen:
    missing.append("no reviewer was invoked")
if cfg["require_qa_verifier"]:
    required = cfg.get("required_reviewer_slug", "qa-verifier")
    if required not in reviewers_seen:
        missing.append(f"required reviewer '{required}' was not invoked")

if cfg.get("require_regression_passed", False):
    if not last_regression_ok:
        missing.append(
            "real regression run has not passed this task. "
            "Run: python3 harmonist/agents/scripts/run_regression.py"
            + (f"   (last run at {last_regression_at})"
               if last_regression_at else "")
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
            fp = pathlib.Path(p)
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

# Run the validator on every touched memory file. We call validate.py
# (sibling of memory.py) directly because it supports --quiet.
if memory_cli and memory_updates:
    validator = pathlib.Path(memory_cli).with_name("validate.py")
    if validator.exists():
        rc = subprocess.run(
            ["python3", str(validator), "--strict", "--quiet"],
            capture_output=True, text=True,
        )
        if rc.returncode != 0:
            missing.append("memory files failed schema validation:\n" + rc.stderr.strip())

if not missing:
    emit_allow("protocol-satisfied")
    sys.exit(0)

# Fail-closed on loop_limit exhaustion. Cursor's hooks.json sets
# `loop_limit: 3`; a stubborn agent could otherwise repeat its final
# message N times, the gate would emit N followups, then Cursor would
# silently surface the last response WITHOUT us ever getting another
# turn to flip the state. We detect the "about to hit the limit" case
# here (attempts already N-1 -> this one would be the N-th) and
# switch to `emit_exhausted`, which records an incident and bumps
# task_seq rather than accumulating stale writes forever.
_LOOP_LIMIT = int(cfg.get("loop_limit", 3))
if int(state.get("enforcement_attempts", 0)) + 1 >= _LOOP_LIMIT:
    emit_exhausted(missing)
    sys.exit(0)

wrote = ", ".join(w["path"] for w in writes[:5])
more = "" if len(writes) <= 5 else f" (+{len(writes) - 5} more)"
seen = ", ".join(sorted(reviewers_seen)) or "(none)"
issues = "\n".join(f"  - {m}" for m in missing)

followup = (
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
emit_followup(followup)
PY
