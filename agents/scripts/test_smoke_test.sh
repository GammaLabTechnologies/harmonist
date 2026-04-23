#!/usr/bin/env bash
# Tests for smoke_test.py: build a fully-integrated fixture project,
# run the smoke driver, verify it reports success. Also run on a half-
# installed project to verify the preflight bail-out.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/smoke_test.py"
UPGRADE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/upgrade.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. Fully integrated project -> both scenarios pass, exit 0.
# ---------------------------------------------------------------------------

printf "\n=== 1: fully integrated project ===\n"
proj="$TMP/full"; mkdir -p "$proj"
cp "$PACK/AGENTS.md" "$proj/AGENTS.md"
python3 "$UPGRADE" --project "$proj" --pack "$PACK" --apply >/dev/null || true

set +e
out="$(python3 "$SCRIPT" --project "$proj" 2>&1)"
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "smoke exits 0 on clean install" || ko "exit rc=$rc, out head=${out:0:160}"

# The happy scenario must report at least 6 passing steps.
happy_ok="$(printf '%s' "$out" | grep -c 'ok  ' || true)"
(( happy_ok >= 6 )) && ok "happy path reports >=6 passing steps" || ko "only $happy_ok ok steps"

# No FAIL markers
set +e
if printf '%s' "$out" | grep -q 'FAIL'; then
  ko "FAIL markers present in output"
else
  ok "no FAIL markers"
fi
set -e

# ---------------------------------------------------------------------------
# 2. JSON mode parses and reports zero failed steps.
# ---------------------------------------------------------------------------

printf "\n=== 2: JSON output ===\n"
set +e
json_out="$(python3 "$SCRIPT" --project "$proj" --json 2>&1)"
set -e
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['summary']['failed_steps'] == 0, d['summary']
assert len(d['scenarios']) == 2
" >/dev/null 2>&1; then
  ok "JSON payload parses, 0 failures, 2 scenarios"
else
  ko "JSON invalid or non-zero failures"
fi

# ---------------------------------------------------------------------------
# 3. Half-installed project (no hooks) -> preflight bail-out with exit 2.
# ---------------------------------------------------------------------------

printf "\n=== 3: preflight bail-out when hooks missing ===\n"
broken="$TMP/broken"; mkdir -p "$broken"
cp "$PACK/AGENTS.md" "$broken/AGENTS.md"
# Create ONLY AGENTS.md -- no .cursor at all
set +e
python3 "$SCRIPT" --project "$broken" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "preflight bails with exit 2" || ko "rc=$rc (expected 2)"

# ---------------------------------------------------------------------------
# 4. Missing AGENTS.md -> exit 2 with a clear message.
# ---------------------------------------------------------------------------

printf "\n=== 4: no AGENTS.md -> exit 2 ===\n"
noagents="$TMP/noagents"; mkdir -p "$noagents"
set +e
python3 "$SCRIPT" --project "$noagents" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "missing AGENTS.md bails with exit 2" || ko "rc=$rc"

# ---------------------------------------------------------------------------
# 5. Stubbed-out reviewer path: negative scenario alone (we already exercise
#    it in scenario #1). Re-run happy scenario by deleting state manually --
#    must still pass (idempotent).
# ---------------------------------------------------------------------------

printf "\n=== 5: re-run is idempotent ===\n"
rm -rf "$proj/.cursor/hooks/.state"
set +e
python3 "$SCRIPT" --project "$proj" >/dev/null
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "second run still exits 0" || ko "rc=$rc"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
