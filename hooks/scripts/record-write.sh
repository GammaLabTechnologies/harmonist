#!/usr/bin/env bash
# afterFileEdit hook — logs every file write so the stop gate knows whether
# protocol-enforced reviews are required before the agent may finish.

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin

state_update '
import re, time
file_path = INPUT.get("file_path") or INPUT.get("path") or ""
if not file_path:
    pass
else:
    entry = {"path": file_path, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Memory files are tracked separately from source writes.
    if file_path in CFG["memory_paths"] or any(file_path.endswith(m.split("/")[-1]) for m in CFG["memory_paths"]):
        STATE["memory_updates"].append(entry)
    else:
        # Skip infrastructure paths (generated, vendored, or hook-internal).
        skip = any(re.search(p, file_path) for p in CFG["skip_path_patterns"])
        if not skip:
            STATE["writes"].append(entry)

            # Capability-scoping: if any readonly subagent is currently
            # active (between subagentStart and subagentStop), the write
            # is a violation.  The gate refuses to finish the turn until
            # the violation is acknowledged.
            actives = STATE.get("active_readonly_subagents") or []
            if actives:
                violation = {
                    **entry,
                    "violator_slugs": list(actives),
                }
                STATE.setdefault("readonly_violations", []).append(violation)
'

log_event "afterFileEdit: $(json_get file_path)"
emit_allow
