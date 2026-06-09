#!/usr/bin/env bash
# subagentStart hook — parses the subagent prompt for the mandatory
# 'AGENT: <slug>' marker and records which agent was delegated to.
#
# If the marker is missing, we do NOT block: the marker contract is advisory
# at subagentStart. The real enforcement happens at 'stop' — if the task
# required reviewers and none were detected, the stop gate blocks completion
# there (where the orchestrator still has a chance to redo the task).

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin

# Mechanical concurrency cap: deny a new subagent launch while
# max_concurrent_subagents are already open. Mirrors hook_runner.py so the
# POSIX and Python paths behave identically. Counts open (not-completed,
# not-stale) calls; emits {"permission":"deny"} when the cap is hit.
cap_decision="$(STATE_FILE_PATH="$STATE_FILE" CFG_JSON="$(read_cfg)" python3 - <<'PY'
import json, os, time, calendar, pathlib
cfg = json.loads(os.environ.get("CFG_JSON", "{}"))
cap = int(cfg.get("max_concurrent_subagents", 3) or 0)
if cap <= 0:
    print("OK"); raise SystemExit
try:
    state = json.loads(pathlib.Path(os.environ["STATE_FILE_PATH"]).read_text())
except Exception:
    print("OK"); raise SystemExit
stale = int(cfg.get("subagent_stale_seconds", 900) or 0)
now = time.time()
active = 0
for call in state.get("subagent_calls", []):
    if call.get("completed") or call.get("stopped_at"):
        continue
    ts = call.get("at") or call.get("started_at") or ""
    if stale > 0 and ts:
        try:
            started = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
            if (now - started) > stale:
                continue
        except Exception:
            pass
    active += 1
print(f"DENY {active} {cap}" if active >= cap else "OK")
PY
)"
if [[ "$cap_decision" == DENY* ]]; then
  read -r _ active cap <<< "$cap_decision"
  log_event "subagentStart DENIED: ${active} active >= cap ${cap}"
  bump_telemetry_counter "summaries.subagent_cap_denials"
  emit_deny "Concurrent-subagent limit reached: ${active} subagent(s) already running (max_concurrent_subagents=${cap}). Running too many subagents in parallel can exhaust memory, especially on a 1M-context model. Wait for an active subagent to finish, then dispatch the next one. Raise max_concurrent_subagents in .cursor/hooks/config.json to allow more."
  exit 0
fi

# Delegation-context gate (opt-in): deny a marker-only / contextless task.
deleg_decision="$(CFG_JSON="$(read_cfg)" STDIN_JSON="$STDIN_JSON" python3 - <<'PY'
import json, os, re
cfg = json.loads(os.environ.get("CFG_JSON", "{}"))
if not cfg.get("require_delegation_context", False):
    print("OK"); raise SystemExit
try:
    inp = json.loads(os.environ.get("STDIN_JSON", "") or "{}")
except Exception:
    inp = {}
prompt = ""
for k in ("prompt", "task", "description", "input", "message"):
    v = inp.get(k)
    if isinstance(v, str) and v.strip():
        prompt = v
        break
if isinstance(inp.get("tool_input"), dict):
    for k in ("prompt", "task", "description"):
        v = inp["tool_input"].get(k)
        if isinstance(v, str) and v.strip():
            prompt = v
            break
slug = None
m = re.search(r"^\s*AGENT:\s*([a-z0-9][a-z0-9-]*)", prompt or "", re.MULTILINE)
if m:
    slug = m.group(1)
if not slug:
    print("OK"); raise SystemExit  # no marker -> handled elsewhere
body = re.sub(r"(?im)^\s*AGENT:\s*\S+\s*$", "", prompt or "")
body = re.sub(r"<!--\s*AGENT:[^>]*-->", "", body)
min_chars = int(cfg.get("min_delegation_chars", 80) or 0)
print("DENY" if len(body.strip()) < min_chars else "OK")
PY
)"
if [[ "$deleg_decision" == DENY ]]; then
  log_event "subagentStart DENIED: thin delegation (require_delegation_context)"
  bump_telemetry_counter "summaries.delegation_context_denials"
  emit_deny "Thin delegation: a subagent only sees the prompt you pass, not your conversation. Include the handoff package — target/scope, the single sub-goal, constraints (authorization boundary, what NOT to do), success criteria — plus the PROJECT PRECEDENCE preamble. Re-dispatch with that context. (Disable via require_delegation_context in .cursor/hooks/config.json.)"
  exit 0
fi

state_update '
import os, pathlib, re, time

prompt = ""
for key in ("prompt", "task", "description", "input", "message"):
    v = INPUT.get(key)
    if isinstance(v, str) and v.strip():
        prompt = v
        break
if isinstance(INPUT.get("tool_input"), dict):
    for key in ("prompt", "task", "description"):
        v = INPUT["tool_input"].get(key)
        if isinstance(v, str) and v.strip():
            prompt = v
            break

slug = None
# Primary marker: bare "AGENT: <slug>" at the start of a line.
m = re.search(r"^\s*AGENT:\s*([a-z0-9][a-z0-9-]*)", prompt or "", re.MULTILINE)
if m:
    slug = m.group(1).strip().lower()
# Fallback 1: HTML-comment form. Useful when something in the host
# rewriter strips leading lines and we still want the hook to credit
# the reviewer: <!-- AGENT: qa-verifier -->
if not slug and prompt:
    m = re.search(r"<!--\s*AGENT:\s*([a-z0-9][a-z0-9-]*)\s*-->", prompt[:1500])
    if m:
        slug = m.group(1).strip().lower()
# Fallback 2: XML-style tag (<agent>qa-verifier</agent>), same idea.
if not slug and prompt:
    m = re.search(r"<agent[^>]*>\s*([a-z0-9][a-z0-9-]*)\s*</agent>",
                  prompt[:1500], re.IGNORECASE)
    if m:
        slug = m.group(1).strip().lower()

# Capability scoping: if the invoked agent declares `readonly: true`
# in its frontmatter, add it to `active_readonly_subagents`. Writes
# while the list is non-empty are violations; `record-write.sh`
# records them, `gate-stop.sh` blocks on them.
readonly_flag = False
if slug:
    # Agents install under .cursor/agents/, often in category subfolders
    # (e.g. .cursor/agents/review/<slug>.md), so a flat lookup misses them
    # and a readonly reviewer that writes would go undetected (fail-open).
    # Check the flat path first, then search recursively.
    agents_dir = pathlib.Path(".cursor") / "agents"
    agent_file = None
    flat = agents_dir / (slug + ".md")
    if flat.exists():
        agent_file = flat
    elif agents_dir.exists():
        agent_file = next((p for p in agents_dir.rglob(slug + ".md")), None)
    if agent_file is not None:
        try:
            text = agent_file.read_text(errors="replace")
            mfm = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
            if mfm:
                for line in mfm.group(1).splitlines():
                    mr = re.match(r"^readonly:\s*(true|True|yes)\s*$", line)
                    if mr:
                        readonly_flag = True
                        break
        except Exception:
            pass

entry = {
    "subagent_type": INPUT.get("subagent_type") or INPUT.get("type") or "unknown",
    "slug": slug,
    "readonly": readonly_flag,
    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "prompt_len": len(prompt or ""),
}
STATE["subagent_calls"].append(entry)

if slug and readonly_flag:
    actives = STATE.setdefault("active_readonly_subagents", [])
    if slug not in actives:
        actives.append(slug)
'

# Read the slug the python block above extracted, then log it under the
# CORRECT label. (This line used to log `slug=$(json_get subagent_type)`,
# which mislabelled the subagent TYPE -- e.g. generalPurpose -- as the slug.)
slug="$(state_read | python3 -c 'import json,sys
d=json.load(sys.stdin)
calls=d.get("subagent_calls") or []
if calls: print(calls[-1].get("slug") or "")' 2>/dev/null || true)"

log_event "subagentStart slug=${slug:-"(none)"} type=$(json_get subagent_type)"

# Telemetry: bump per-slug invocation counter if the AGENT: marker was present.
if [[ -n "$slug" && "$slug" != "None" ]]; then
  bump_telemetry_agent "$slug"
fi

emit_allow
