#!/usr/bin/env bash
# afterFileEdit hook — logs every file write so the stop gate knows whether
# protocol-enforced reviews are required before the agent may finish.

set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

read_stdin

# MEMORY_CLI lets the classifier resolve the real memory dir (the directory
# containing memory.py) so absolute paths to it are recognised; mirrors
# hook_runner.py::_is_memory_update_path.
MEMORY_CLI="$(memory_cli_path)" state_update '
import os, pathlib, re, time
file_path = INPUT.get("file_path") or INPUT.get("path") or ""

def _is_memory_update_path(path):
    # Memory classification requires the file to live in the memory DIR,
    # not merely share a basename: a bare basename match let
    # `frontend/patterns.md` bypass the review gate, and let any random
    # session-handoff.md satisfy the stop gate.
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    base = p.split("/")[-1]
    mem_paths = [str(m) for m in CFG.get("memory_paths", [])]
    names = set()
    for m in mem_paths:
        mm = m.replace("\\", "/").rstrip("/")
        names.add(mm.split("/")[-1])
    if base not in names:
        return False
    for m in mem_paths:
        mm = m.replace("\\", "/")
        while mm.startswith("./"):
            mm = mm[2:]
        if p == mm or p.endswith("/" + mm):
            return True
    try:
        parent = pathlib.Path(path).resolve().parent
    except Exception:
        return False
    cli = os.environ.get("MEMORY_CLI", "")
    if cli:
        try:
            if parent == pathlib.Path(cli).resolve().parent:
                return True
        except Exception:
            pass
    for m in mem_paths:
        mp = pathlib.Path(m)
        cand = mp if mp.is_absolute() else (pathlib.Path.cwd() / mp)
        try:
            if parent == cand.resolve().parent:
                return True
        except Exception:
            continue
    return False

if not file_path:
    pass
else:
    entry = {"path": file_path, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # Memory files are tracked separately from source writes.
    if _is_memory_update_path(file_path):
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
