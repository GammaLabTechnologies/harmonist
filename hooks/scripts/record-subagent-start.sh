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
    # The hook lives at .cursor/hooks/scripts/record-subagent-start.sh
    # and agent files are at .cursor/agents/<slug>.md. Walk up two
    # levels from this script to find .cursor/.
    for candidate in (
        pathlib.Path(".cursor") / "agents" / (slug + ".md"),
    ):
        if candidate.exists():
            try:
                text = candidate.read_text(errors="replace")
                mfm = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
                if mfm:
                    for line in mfm.group(1).splitlines():
                        mr = re.match(r"^readonly:\s*(true|True|yes)\s*$", line)
                        if mr:
                            readonly_flag = True
                            break
            except Exception:
                pass
            break

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

log_event "subagentStart slug=$(json_get subagent_type) type=$(json_get type)"

# Telemetry: bump per-slug invocation counter if the AGENT: marker was
# present. Reading it back out of state is simpler than re-parsing the
# prompt -- the python block above already extracted it.
slug="$(state_read | python3 -c 'import json,sys
d=json.load(sys.stdin)
calls=d.get("subagent_calls") or []
if calls: print(calls[-1].get("slug") or "")' 2>/dev/null || true)"
if [[ -n "$slug" && "$slug" != "None" ]]; then
  bump_telemetry_agent "$slug"
fi

emit_allow
