#!/usr/bin/env bash
# subagentStop hook — when a subagent completes, check whether its slug
# (captured at subagentStart) belongs to the reviewer set. If yes, mark
# the reviewer as 'seen' so the stop gate is satisfied.

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin

state_update '
# Find the most recent subagent call that has not yet been marked as completed.
# (We match by subagent_type + order; prompt_len gives us extra disambiguation
# if the same type was used twice.)
pending = None
for call in reversed(STATE["subagent_calls"]):
    if not call.get("completed"):
        pending = call
        break
if pending is not None:
    pending["completed"] = True
    slug = pending.get("slug")
    if slug and slug in CFG["reviewer_slugs"] and slug not in STATE["reviewers_seen"]:
        STATE["reviewers_seen"].append(slug)
    # Capability scoping: pop this slug off `active_readonly_subagents`
    # so subsequent writes outside this invocation arent flagged.
    if slug:
        actives = STATE.get("active_readonly_subagents") or []
        if slug in actives:
            actives.remove(slug)
            STATE["active_readonly_subagents"] = actives
'

log_event "subagentStop reviewers_seen=$(state_read | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin)[\"reviewers_seen\"]))' 2>/dev/null || echo "?")"
emit_allow
