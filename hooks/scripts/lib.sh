#!/usr/bin/env bash
# Shared helpers for harmonist enforcement hooks.
#
# Exposes:
#   HOOKS_DIR / STATE_DIR / STATE_FILE  — where session state lives
#   read_stdin                          — slurp full stdin into $STDIN_JSON
#   json_get <path>                     — print value at dotted path in $STDIN_JSON
#   state_init / state_reset / state_read
#   state_update <script>               — atomic mutation via python; script sees
#                                         CFG (dict) and STATE (dict), may mutate STATE
#   read_cfg                            — dump effective config (defaults + overrides)
#   log_event <msg...>                  — append timestamped line to activity.log
#   emit_allow / emit_followup / emit_additional_context
#
# No external dependencies beyond bash 3.2+, python3, and stdin/stdout.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$HOOKS_DIR/.state"
STATE_FILE="$STATE_DIR/session.json"
CFG_FILE="$HOOKS_DIR/config.json"
LOG_FILE="$STATE_DIR/activity.log"

# Telemetry lives next to memory -- local, never synced, .gitignored.
# Can be disabled entirely via CFG.telemetry_enabled = false.
# AGENT_PACK_TELEMETRY_DIR env var overrides location (used by hook tests
# so they don't pollute the pack repo).
if [[ -n "${AGENT_PACK_TELEMETRY_DIR:-}" ]]; then
  TELEMETRY_DIR="$AGENT_PACK_TELEMETRY_DIR"
else
  TELEMETRY_DIR="$(cd "$HOOKS_DIR/.." 2>/dev/null && pwd)/telemetry"
fi
TELEMETRY_FILE="$TELEMETRY_DIR/agent-usage.json"

mkdir -p "$STATE_DIR"

# Slurp stdin once; scripts then call json_get to query it.
# Deliberately NOT exported: the payload can contain the full prompt text,
# and a global export would leak it into the environment of EVERY child
# process (memory.py, project_context.py, repomap, ...). Helpers that need
# it receive it as a command-scoped env var instead.
read_stdin() {
  STDIN_JSON="$(cat)"
}

# json_get <dotted.path> — print scalar, or JSON-encoded sub-tree, or nothing.
# Safe for any input: the payload arrives via env var, the path via argv.
json_get() {
  local path="${1:-}"
  STDIN_JSON="${STDIN_JSON:-}" python3 - "$path" <<'PY' 2>/dev/null || true
import json, os, sys
path = sys.argv[1]
raw = os.environ.get("STDIN_JSON", "")
try:
    data = json.loads(raw) if raw else {}
except Exception:
    sys.exit(0)
cur = data
for part in path.split("."):
    if not part:
        continue
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        sys.exit(0)
if isinstance(cur, (dict, list)):
    print(json.dumps(cur))
elif cur is None:
    pass
else:
    print(cur)
PY
}

# --- State file ------------------------------------------------------------

_state_bootstrap() {
  python3 - "$STATE_FILE" <<'PY'
import json, os, sys, time, pathlib
path = pathlib.Path(sys.argv[1])
# session_id = <unix-seconds><pid4> -- the pid suffix makes two Cursor
# windows that bootstrap in the same second collide-proof. All digits,
# so the memory CORRELATION_RE (^\d+-\d+$) still matches.
session_id = f"{int(time.time())}{os.getpid() % 10000:04d}"
state = {
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
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(state, indent=2), encoding="utf-8")
PY
}

# Find the path to the memory CLI so scripts can call it regardless of where
# the user installed the pack. Order of preference:
#   1. $AGENT_PACK_MEMORY_CLI  — explicit override (used by tests and by
#      projects that store memory in a non-default location)
#   2. `.cursor/memory/memory.py` walking up from $HOOKS_DIR (post-integration)
#   3. `<pack>/memory/memory.py` (pack-development mode)
memory_cli_path() {
  if [[ -n "${AGENT_PACK_MEMORY_CLI:-}" && -f "$AGENT_PACK_MEMORY_CLI" ]]; then
    printf '%s' "$AGENT_PACK_MEMORY_CLI"
    return
  fi
  local cur="$HOOKS_DIR"
  for _ in 1 2 3 4 5 6; do
    local candidate="$cur/.cursor/memory/memory.py"
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return
    fi
    cur="$(dirname "$cur")"
  done
  local pack_mem="$(dirname "$HOOKS_DIR")/memory/memory.py"
  [[ -f "$pack_mem" ]] && printf '%s' "$pack_mem" || printf ''
}

# Task sequencing: advance task_seq in state AND in the memory CLI (which
# writes the same state file). Called by the stop gate when a task is
# successfully completed so the next task gets a fresh correlation_id.
bump_task_seq() {
  state_update '
STATE["task_seq"] = int(STATE.get("task_seq", 0)) + 1
sid = STATE["session_id"]
tseq = STATE["task_seq"]
STATE["active_correlation_id"] = str(sid) + "-" + str(tseq)
# Clear per-task buckets so the next task starts clean. OPEN subagent
# calls survive the bump (mirrors hook_runner._bump_task): a still-running
# background readonly reviewer must keep its record or its slug never
# leaves active_readonly_subagents.
STATE["writes"] = []
STATE["subagent_calls"] = [
    c for c in STATE.get("subagent_calls", [])
    if not (c.get("stopped_at") or c.get("completed"))
]
STATE["reviewers_seen"] = []
STATE["memory_updates"] = []
STATE["enforcement_attempts"] = 0
STATE["protocol_skipped"] = False
STATE.pop("protocol_skip_reason", None)
'
}

state_init() {
  [[ -f "$STATE_FILE" ]] || _state_bootstrap
}

state_reset() {
  rm -f "$STATE_FILE"
  _state_bootstrap
}

state_read() {
  state_init
  cat "$STATE_FILE"
}

# state_update <python-script>
# The script is passed as a single argv; inside it STATE is a dict loaded from
# the state file, CFG is the effective config, and the hook input is available
# in the env var STDIN_JSON (raw text). After the script runs, STATE is
# atomically persisted back to disk.
state_update() {
  local script="${1:-}"
  state_init
  CFG_JSON="$(read_cfg)" STATE_FILE_PATH="$STATE_FILE" SCRIPT="$script" \
    STDIN_JSON="${STDIN_JSON:-}" \
    python3 - <<'PY'
import json, os, tempfile, pathlib
state_path = pathlib.Path(os.environ["STATE_FILE_PATH"])
# Serialize the whole read-modify-write across concurrent hook processes so a
# parallel afterFileEdit / subagentStart can't lose an update (last-writer-wins
# would silently drop a recorded write and fail the gate OPEN). Advisory
# fcntl lock on a sidecar .lock; best-effort (proceed if unavailable).
_lock = None
try:
    import fcntl
    _lock = open(str(state_path) + ".lock", "a+")
    fcntl.flock(_lock.fileno(), fcntl.LOCK_EX)
except Exception:
    _lock = None
try:
    STATE = json.loads(state_path.read_text(encoding="utf-8"))
    CFG = json.loads(os.environ.get("CFG_JSON", "{}"))
    STDIN_JSON = os.environ.get("STDIN_JSON", "")
    try:
        INPUT = json.loads(STDIN_JSON) if STDIN_JSON else {}
    except Exception:
        INPUT = {}
    exec(os.environ.get("SCRIPT", ""), {"STATE": STATE, "CFG": CFG, "INPUT": INPUT, "json": json})
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=state_path.parent,
                                      encoding="utf-8")
    json.dump(STATE, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, state_path)
finally:
    if _lock is not None:
        try:
            import fcntl
            fcntl.flock(_lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        _lock.close()
PY
}

# --- Config ----------------------------------------------------------------

read_cfg() {
  CFG_FILE_PATH="$CFG_FILE" python3 - <<'PY'
import json, os, pathlib
DEFAULT = {
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
        "wcag-a11y-gate",
    ],
    "required_reviewer_slug": "qa-verifier",
    # Real CI-runner gate. When enabled, the stop hook also checks
    # `state.last_regression_ok` and refuses to finish until a real
    # regression run (via run_regression.py) has succeeded for the
    # current task. Default: false -- opt in per project once the
    # detected commands actually work in the local dev env.
    "require_regression_passed": False,
    # Lightweight-mode: writes restricted to these path globs do not
    # require a reviewer. Defaults cover docs / config scaffolding that
    # is safe to merge without a full protocol loop. Disable by setting
    # `allow_trivial_without_review`: false, or tighten by overriding
    # `trivial_path_patterns` in the project's `.cursor/hooks/config.json`.
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
        # docs/ only trivial for doc/asset content, not code under it
        # (a bare `docs/` substring let docs/conf.py skip the gate).
        r"(?i)(^|/)docs/.*\.(?:md|mdx|rst|txt|adoc|markdown|png|jpe?g|gif|svg|webp|ico|pdf)$",
        r"(?i)(^|/)documentation/.*\.(?:md|mdx|rst|txt|adoc|markdown|png|jpe?g|gif|svg|webp|ico|pdf)$",
    ],
    # Mechanical cap on concurrently-running subagents within one task.
    # The subagentStart hook denies a launch once this many are open.
    # 0 (or negative) disables the cap. Mirrors hook_runner.py.
    "max_concurrent_subagents": 3,
    # Open subagents older than this (no observed stop) are treated as
    # finished for the count, so a missed stop can't lock out new launches.
    "subagent_stale_seconds": 900,
    # Impact-aware gate + repo-map staleness banner. The .sh gate path does
    # not implement these (the Python hook_runner is the active path); the
    # keys live here so config overrides validate cleanly.
    "require_affected_tests": False,
    "repomap_staleness_warn": True,
    "require_delegation_context": False,
    "min_delegation_chars": 80,
    # HITL on dangerous shell commands (handled by the Python hook runner's
    # beforeShellExecution phase). Keys live here so config overrides validate.
    "hitl_enabled": True,
    "dangerous_command_action": "ask",
}
cfg_path = pathlib.Path(os.environ.get("CFG_FILE_PATH", ""))
if cfg_path.exists():
    try:
        user = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        # Silently falling back to defaults would make the operator believe
        # their overrides are active. One line on stderr, then defaults.
        import sys
        print(f"hooks: WARNING: {cfg_path} is malformed JSON; using default config",
              file=sys.stderr)
        user = {}
    DEFAULT.update(user)
print(json.dumps(DEFAULT))
PY
}

# --- Logging ---------------------------------------------------------------

# Cap activity.log growth (mirrors hook_runner.py): past ~1MiB, keep only the
# most recent half so a long-lived project never accumulates an unbounded log.
LOG_MAX_BYTES=1048576

_rotate_log_if_needed() {
  [[ -f "$LOG_FILE" ]] || return 0
  local size
  size=$(wc -c <"$LOG_FILE" 2>/dev/null | tr -d ' ') || return 0
  [[ -n "$size" && "$size" -gt "$LOG_MAX_BYTES" ]] || return 0
  local tmp="$LOG_FILE.tmp.$$"
  {
    printf '[log rotated: older half discarded]\n'
    tail -c $((LOG_MAX_BYTES / 2)) "$LOG_FILE" | sed '1d'
  } >"$tmp" 2>/dev/null && mv "$tmp" "$LOG_FILE" || rm -f "$tmp"
}

log_event() {
  local msg="$*"
  mkdir -p "$(dirname "$LOG_FILE")"
  _rotate_log_if_needed
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >>"$LOG_FILE"
}

# --- Telemetry -------------------------------------------------------------
# Per-project, local-only usage stats. Never leaves the machine; .gitignored
# by the pack's upgrade script. Disable via CFG.telemetry_enabled = false.

_telemetry_enabled() {
  # Read the config once; default = true if absent.
  CFG_JSON="$(read_cfg)" python3 - <<'PY' 2>/dev/null
import json, os, sys
try:
    cfg = json.loads(os.environ.get("CFG_JSON", "{}"))
except Exception:
    sys.exit(0)  # default-enabled on parse failure
print("1" if cfg.get("telemetry_enabled", True) else "0")
PY
}

# bump_telemetry_counter <key-path> [increment=1]
# Atomically increments a counter in agent-usage.json. key-path is a dotted
# path (e.g. "agents.qa-verifier.invocations" or "summaries.gate_followups").
bump_telemetry_counter() {
  local keypath="${1:-}"
  local inc="${2:-1}"
  [[ -z "$keypath" ]] && return
  [[ "$(_telemetry_enabled)" == "0" ]] && return
  mkdir -p "$TELEMETRY_DIR"
  TELEMETRY_FILE_PATH="$TELEMETRY_FILE" KEYPATH="$keypath" INC="$inc" python3 - <<'PY'
import json, os, pathlib, tempfile, time

path = pathlib.Path(os.environ["TELEMETRY_FILE_PATH"])
keypath = os.environ["KEYPATH"].split(".")
inc = int(os.environ.get("INC", "1"))

try:
    data = json.loads(path.read_text()) if path.exists() else {}
except Exception:
    data = {}
data.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
data.setdefault("agents", {})
data.setdefault("summaries", {})

# Navigate / create nested dicts.
cur = data
for part in keypath[:-1]:
    cur = cur.setdefault(part, {})
last = keypath[-1]
if last == "last_at":
    cur[last] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
else:
    cur[last] = int(cur.get(last, 0)) + inc

# Always stamp last-overall timestamp.
data["last_update_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent)
json.dump(data, tmp, indent=2, sort_keys=True)
tmp.close()
os.replace(tmp.name, path)
PY
}

# bump_telemetry_agent <slug>
# Convenience wrapper: increments both the per-slug counter and stamps last_at.
bump_telemetry_agent() {
  local slug="${1:-}"
  [[ -z "$slug" ]] && return
  bump_telemetry_counter "agents.$slug.invocations"
  bump_telemetry_counter "agents.$slug.last_at"
}

# --- Hook response helpers -------------------------------------------------

emit_allow() {
  printf '{}\n'
}

emit_followup() {
  local msg="${1:-}"
  MSG="$msg" python3 - <<'PY'
import json, os
print(json.dumps({"followup_message": os.environ.get("MSG", "")}))
PY
}

emit_additional_context() {
  local msg="${1:-}"
  MSG="$msg" python3 - <<'PY'
import json, os
print(json.dumps({"additional_context": os.environ.get("MSG", "")}))
PY
}

# emit_deny <message> — block the pending action (e.g. a subagent launch).
# Per Cursor's hook contract, subagentStart honours {"permission": "deny"}.
emit_deny() {
  local msg="${1:-}"
  MSG="$msg" python3 - <<'PY'
import json, os
m = os.environ.get("MSG", "")
print(json.dumps({"permission": "deny", "user_message": m, "agent_message": m}))
PY
}
