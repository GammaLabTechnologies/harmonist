#!/usr/bin/env bash
# sessionStart hook — resets enforcement state, seeds the agent with the
# latest memory so it cannot 'forget' to read session-handoff, and surfaces
# the active correlation_id for this task.

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin

# BEFORE state_reset wipes session.json: read the cross-session
# incidents.json so we can surface protocol-exhausted warnings to the
# user in this session's bootstrap context. The gate-stop hook writes
# an entry here whenever it hits loop_limit (fail-closed path).
incidents_banner=""
INCIDENTS_FILE="$STATE_DIR/incidents.json"
if [[ -f "$INCIDENTS_FILE" ]]; then
  incidents_banner="$(INCIDENTS_FILE="$INCIDENTS_FILE" python3 - <<'PY' 2>/dev/null || true
import json, os, sys
try:
    data = json.loads(open(os.environ["INCIDENTS_FILE"]).read())
except Exception:
    sys.exit(0)
incidents = data.get("incidents") or []
if not incidents:
    sys.exit(0)
# Only surface incidents that have NOT yet been acknowledged. We mark
# them as surfaced by setting "surfaced_at". The file is rewritten.
unsurfaced = [i for i in incidents if not i.get("surfaced_at")]
if not unsurfaced:
    sys.exit(0)
import time as _t
now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
lines = [
    "",
    "!!  PROTOCOL-EXHAUSTED incident(s) from previous session(s):",
]
for inc in unsurfaced[-3:]:
    cid = inc.get("correlation_id", "?")
    missing = inc.get("missing", [])
    writes = inc.get("writes", [])
    lines.append(f"    - cid={cid}  writes={writes[:3]}  missing={missing[:2]}")
lines.append(
    "    These tasks were force-closed after the gate retried\n"
    "    loop_limit times without protocol satisfaction. Writes may\n"
    "    be in an inconsistent state (no reviewer, no handoff entry).\n"
    "    Investigate the listed correlation_id(s) before starting new\n"
    "    code changes. Full log: .cursor/hooks/.state/activity.log\n"
)
print("\n".join(lines))
# Mark all as surfaced so the next session does not re-announce.
for inc in incidents:
    inc.setdefault("surfaced_at", now)
import tempfile
tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=os.path.dirname(os.environ["INCIDENTS_FILE"]))
json.dump(data, tmp, indent=2)
tmp.close()
os.replace(tmp.name, os.environ["INCIDENTS_FILE"])
PY
)"
fi

state_reset
log_event "sessionStart: state reset"
bump_telemetry_counter "summaries.sessions"

MEMORY_CLI="$(memory_cli_path)"
active_cid="$(state_read | python3 -c 'import json,sys;print(json.load(sys.stdin).get("active_correlation_id",""))')"

# PROTOCOL-SKIP abuse detection. The marker is a legitimate escape hatch
# for genuinely trivial turns but it is SELF-REPORTED -- the LLM can drop
# it on every task and silently bypass the review gate. We don't block
# the skip (the gate-stop hook must stay fast), but we surface a visible
# warning in the session bootstrap when the skip ratio exceeds
# `protocol_skip_warn_threshold_ratio` AND the absolute count is at
# least `protocol_skip_warn_threshold_count`. Both are tunable via
# config; disable entirely with `"protocol_skip_warn_enabled": false`.
protocol_skip_warning=""
if [[ -f "$TELEMETRY_FILE" ]]; then
  protocol_skip_warning="$(CFG_JSON="$(read_cfg)" TEL_FILE="$TELEMETRY_FILE" python3 - <<'PY' 2>/dev/null || true
import json, os, sys
try:
    cfg = json.loads(os.environ.get("CFG_JSON", "{}") or "{}")
except Exception:
    cfg = {}
if not cfg.get("protocol_skip_warn_enabled", True):
    sys.exit(0)
min_count = int(cfg.get("protocol_skip_warn_threshold_count", 5))
min_ratio = float(cfg.get("protocol_skip_warn_threshold_ratio", 0.25))
try:
    tel = json.loads(open(os.environ["TEL_FILE"]).read())
except Exception:
    sys.exit(0)
s = tel.get("summaries") or {}
skips = int(s.get("protocol_skips", 0))
satisfied = int(s.get("gate_allow_satisfied", 0))
total_allow = skips + satisfied
if skips < min_count or total_allow == 0:
    sys.exit(0)
ratio = skips / total_allow
if ratio < min_ratio:
    sys.exit(0)
pct = int(round(ratio * 100))
print(
    "\n!!  PROTOCOL-SKIP audit: "
    + str(skips) + " of the last " + str(total_allow)
    + " completions used PROTOCOL-SKIP (" + str(pct) + "%).\n"
    "    The marker is for genuinely trivial turns (typo / comment).\n"
    "    If you are using it to bypass qa-verifier on real code\n"
    "    changes, STOP and run the full gate. Abuse is visible in\n"
    "    .cursor/hooks/.state/activity.log.\n"
)
PY
)"
fi

# Pull the latest 3 state entries and the latest 3 decisions — enough to
# restore context without blowing up the prompt.
latest_state=""
latest_decisions=""
if [[ -n "$MEMORY_CLI" && -f "$MEMORY_CLI" ]]; then
  latest_state="$(python3 "$MEMORY_CLI" latest --file session-handoff --kind state --n 3 2>/dev/null || true)"
  latest_decisions="$(python3 "$MEMORY_CLI" latest --file decisions --kind decision --n 3 2>/dev/null || true)"
fi

# Project precedence: surface the AGENTS.md invariants/stack/modules so
# the session owner sees the authoritative rules BEFORE any subagent is
# called. This is the mechanical answer to "persona says X, AGENTS.md
# says Y" -- authority lives in this injection.
project_context=""
# Walk up from $HOOKS_DIR trying to find a project-scoped project_context.py
# (post-integration layout) or the pack-local copy.
pc_script=""
for cand in \
    "$HOOKS_DIR/../scripts/project_context.py" \
    "$HOOKS_DIR/../.cursor/scripts/project_context.py" \
    "$(dirname "$HOOKS_DIR")/agents/scripts/project_context.py" \
; do
  if [[ -f "$cand" ]]; then
    pc_script="$cand"
    break
  fi
done
if [[ -n "$pc_script" ]]; then
  project_context="$(python3 "$pc_script" --max-chars 1200 2>/dev/null || true)"
fi

msg="harmonist enforcement hooks are active in this session.
${protocol_skip_warning}${incidents_banner}
Active correlation_id for this task: ${active_cid:-unknown}

Mandatory protocol reminders:
1. Project AGENTS.md OVERRIDES any persona agent advice. When a persona
   suggests an approach that conflicts with Invariants / Platform Stack
   / Modules below, follow the project and flag the conflict in your
   response.
2. Delegate subagent work via Task; the FIRST line of every subagent
   prompt MUST be 'AGENT: <slug>' (e.g. 'AGENT: qa-verifier'), and the
   prompt MUST include a project-precedence preamble (use
   'python3 harmonist/agents/scripts/project_context.py' to
   generate it).
3. After any code change: invoke qa-verifier (required) and any further
   reviewers the trigger table in AGENTS.md demands.
4. Append a session-handoff entry AT THE END OF EVERY TASK via the CLI:
     python3 .cursor/memory/memory.py append \\
       --file session-handoff --kind state --status done \\
       --summary '<one line>' --body '<what changed / state / open issues>'
   The stop hook will block completion until an entry with
   correlation_id=${active_cid:-<active_cid>} lands in session-handoff.md.
5. For significant architectural choices, also append a decision entry
   (kind: decision, file: decisions).

${project_context:-(project_context.py could not locate AGENTS.md; working without invariants injection)}

Recent state (last 3 entries from session-handoff.md):
${latest_state:-  (empty — first session or memory not yet seeded)}

Recent decisions (last 3 entries from decisions.md):
${latest_decisions:-  (empty)}"

emit_additional_context "$msg"
