#!/usr/bin/env bash
# Tests for onboard.py: walkthrough + --check + --json across empty,
# partially integrated, and fully integrated fixtures.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ONB="$PACK/agents/scripts/onboard.py"
UP="$PACK/agents/scripts/upgrade.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. Empty dir -> exit 2 + "not integrated" message
# ---------------------------------------------------------------------------

printf "\n=== 1: empty directory ===\n"
empty="$TMP/empty"
mkdir -p "$empty"
set +e
out="$(python3 "$ONB" --project "$empty" 2>&1)"
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "empty exits 2" || ko "empty exit=$rc"
printf '%s' "$out" | /usr/bin/grep -q "NOT look integrated" && ok "emits not-integrated message" || ko "message missing"

# ---------------------------------------------------------------------------
# 2. Fully integrated project via upgrade.py --apply
# ---------------------------------------------------------------------------

printf "\n=== 2: fully integrated project ===\n"
proj="$TMP/proj"
mkdir -p "$proj"
cp "$PACK/AGENTS.md" "$proj/AGENTS.md"
python3 "$UP" --project "$proj" --pack "$PACK" --apply >/dev/null 2>&1 || true

set +e
out="$(python3 "$ONB" --project "$proj" 2>&1)"
rc=$?
set -e
printf '%s' "$out" | /usr/bin/grep -q "Welcome aboard" && ok "walkthrough renders" || ko "walkthrough missing"
printf '%s' "$out" | /usr/bin/grep -q "strict reviewer agents installed" && ok "mentions strict reviewers" || ko "no strict-reviewer line"
printf '%s' "$out" | /usr/bin/grep -q "session-handoff.md" && ok "mentions session-handoff" || ko "no session-handoff reference"

# ---------------------------------------------------------------------------
# 3. --check mode on integrated project passes
# ---------------------------------------------------------------------------

printf "\n=== 3: --check on integrated project ===\n"
set +e
out="$(python3 "$ONB" --project "$proj" --check 2>&1)"
rc=$?
set -e
printf '%s' "$out" | /usr/bin/grep -q "Summary:" && ok "emits check summary" || ko "no summary"

# ---------------------------------------------------------------------------
# 4. --json output is machine-parseable with expected keys
# ---------------------------------------------------------------------------

printf "\n=== 4: --json shape ===\n"
set +e
json_out="$(python3 "$ONB" --project "$proj" --json 2>&1)"
rc=$?
set -e
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'snapshot' in d and 'checks' in d
assert d['snapshot']['integrated'] is True
assert isinstance(d['checks'], list)
assert any(c['name']=='strict-agents' for c in d['checks'])
"; then
  ok "JSON payload has expected shape"
else
  ko "JSON malformed"
fi

# ---------------------------------------------------------------------------
# 5. Partial integration -> --check flags missing pieces
# ---------------------------------------------------------------------------

printf "\n=== 5: partial integration ===\n"
partial="$TMP/partial"
mkdir -p "$partial/.cursor"
cp "$PACK/AGENTS.md" "$partial/AGENTS.md"
# Intentionally omit .cursor/agents, hooks, memory, rules.
set +e
out="$(python3 "$ONB" --project "$partial" --check 2>&1)"
rc=$?
set -e
printf '%s' "$out" | /usr/bin/grep -q "✖" && ok "partial fixture has failing checks" || ko "no failing checks"
[[ "$rc" == "1" ]] && ok "partial exits 1" || ko "partial exit=$rc (expected 1)"

# ---------------------------------------------------------------------------
# 6. Walkthrough on integrated project actually mentions fast-path
# ---------------------------------------------------------------------------

printf "\n=== 6: walkthrough explains lightweight mode ===\n"
out="$(python3 "$ONB" --project "$proj" 2>&1 || true)"
printf '%s' "$out" | /usr/bin/grep -q "lightweight fast-path" && ok "mentions lightweight mode" || ko "lightweight not explained"
printf '%s' "$out" | /usr/bin/grep -q "AGENT:" && ok "mentions AGENT: marker convention" || ko "AGENT: not explained"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
