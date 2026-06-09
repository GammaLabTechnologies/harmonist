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
# Capability scoping: reconcile `active_readonly_subagents` against the
# OPEN call records (mirrors hook_runner.phase_subagent_stop). A slug
# stays active only while at least one of its invocations is still open;
# this also clears stragglers when the stop matched NO open record (e.g.
# a task bump consumed the record before a late background-reviewer stop
# arrived) -- otherwise the readonly flag sticks forever and every later
# edit records an un-remediable violation.
open_slugs = set()
for call in STATE.get("subagent_calls", []):
    if not (call.get("completed") or call.get("stopped_at")):
        s = call.get("slug")
        if s:
            open_slugs.add(s)
actives = STATE.get("active_readonly_subagents") or []
STATE["active_readonly_subagents"] = [s for s in actives if s in open_slugs]
'

log_event "subagentStop reviewers_seen=$(state_read | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin)[\"reviewers_seen\"]))' 2>/dev/null || echo "?")"
emit_allow
