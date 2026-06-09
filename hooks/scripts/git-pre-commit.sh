#!/usr/bin/env bash
# git pre-commit guard -- complements the Cursor `stop` gate.
#
# The stop gate fires INSIDE the IDE when the agent finishes a turn. This hook
# fires on `git commit`, including commits made directly from a terminal -- the
# one path that can otherwise bypass review entirely.
#
# Policy: if the commit stages review-worthy code (NOT matched by
# skip_path_patterns and NOT "trivial" per trivial_path_patterns -- the same
# definition the stop gate uses) AND the current enforcement task has UNREVIEWED
# pending code writes (the required reviewer, default qa-verifier, is not yet in
# reviewers_seen), the commit is refused. An unresolved protocol-exhausted
# incident also blocks.
#
# Absence of enforcement state (fresh clone / CI / no Cursor session) means
# there is no in-flight unreviewed work to catch -> the commit is ALLOWED. The
# guard never hard-blocks a commit just because state is missing.
#
# Requires python3 + bash on PATH. Emergency bypass: git commit --no-verify
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CFG_JSON="$(read_cfg)"

# core.quotepath=false so non-ASCII paths aren't octal-escaped (which would
# make $-anchored patterns miss them and the guard fail open).
STAGED="$(git -c core.quotepath=false diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)"
[[ -z "$STAGED" ]] && exit 0

# review-worthy = NOT skipped AND NOT trivial (mirrors gate-stop.sh).
# Note on broken/empty config: with no patterns loaded, nothing is filtered
# out, so EVERY staged file is treated as review-worthy -- the fail-closed
# behaviour falls out of the filter structure itself (no special-casing).
REVIEW_WORTHY="$(STAGED_LIST="$STAGED" CFG_JSON="$CFG_JSON" python3 - <<'PY'
import json, os, re
cfg = json.loads(os.environ["CFG_JSON"])
skip = [re.compile(p) for p in cfg.get("skip_path_patterns", [])]
trivial = [re.compile(p) for p in cfg.get("trivial_path_patterns", [])]
out = []
for line in os.environ.get("STAGED_LIST", "").splitlines():
    f = line.strip()
    if not f:
        continue
    if any(rx.search(f) for rx in skip):
        continue
    if any(rx.search(f) for rx in trivial):
        continue
    out.append(f)
print("\n".join(out))
PY
)"

# Only docs / data / skipped / trivial paths staged -> nothing to enforce.
[[ -z "$REVIEW_WORTHY" ]] && exit 0

VERDICT="$(STATE_FILE_PATH="$STATE_FILE" CFG_JSON="$CFG_JSON" python3 - <<'PY'
import json, os, pathlib, re, sys
sp = pathlib.Path(os.environ["STATE_FILE_PATH"])
cfg = json.loads(os.environ["CFG_JSON"])
if not sp.exists():
    print("ALLOW::no-state"); sys.exit(0)
try:
    st = json.loads(sp.read_text())
except Exception:
    print("ALLOW::unreadable-state"); sys.exit(0)
writes = st.get("writes", [])
reviewers = set(st.get("reviewers_seen", []))
required = cfg.get("required_reviewer_slug", "qa-verifier")
skip = [re.compile(p) for p in cfg.get("skip_path_patterns", [])]
trivial = [re.compile(p) for p in cfg.get("trivial_path_patterns", [])]
def worthy(p):
    if any(rx.search(p) for rx in skip):
        return False
    if any(rx.search(p) for rx in trivial):
        return False
    return True
pending = [w.get("path", "") for w in writes if worthy(w.get("path", ""))]
if pending and required not in reviewers:
    print(f"BLOCK::current Cursor task has unreviewed code writes "
          f"(e.g. {pending[:3]}); '{required}' was not invoked")
    sys.exit(0)
if st.get("last_task_status") == "protocol-exhausted":
    print(f"BLOCK::a prior task was force-closed as protocol-exhausted "
          f"(cid={st.get('last_exhausted_correlation_id','?')}); investigate first")
    sys.exit(0)
print("ALLOW::clean")
PY
)"

if [[ "$VERDICT" == BLOCK::* ]]; then
  reason="${VERDICT#BLOCK::}"
  {
    printf '\n[pre-commit] BLOCKED by harmonist enforcement guard:\n  %s\n\n' "$reason"
    printf 'Staged review-worthy code:\n%s\n\n' "$REVIEW_WORTHY"
    printf 'Fix: run the required reviewer(s) in Cursor (AGENT: qa-verifier) and\n'
    printf 'finish the turn so the stop-gate clears, then re-commit.\n'
    printf 'Emergency bypass (audited): git commit --no-verify\n\n'
  } >&2
  exit 1
fi

exit 0
