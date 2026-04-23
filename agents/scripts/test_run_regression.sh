#!/usr/bin/env bash
# Tests for run_regression.py + the `require_regression_passed` gate.
#
#   1. run_regression on a project with no manifests -> exit 2.
#   2. run_regression on a pure-python fixture with pytest passing ->
#      exit 0, state updated with last_regression_ok=true.
#   3. Test command that fails -> exit 1, state has last_regression_ok=false.
#   4. `--json` output parses with `ok`, `plan`, `results`.
#   5. Gate integration: when `require_regression_passed: true` and no
#      regression has run, stop emits followup.
#   6. Gate integration: after a successful run_regression, same gate allows.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$PACK/agents/scripts/run_regression.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. Empty project -> exit 2
# ---------------------------------------------------------------------------

printf "\n=== 1: empty project ===\n"
empty="$TMP/empty"
mkdir -p "$empty"
set +e
python3 "$RUNNER" --project "$empty" --no-write >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "exits 2 with no manifests" || ko "exit=$rc"

# ---------------------------------------------------------------------------
# 2. Python fixture with a passing test -> exit 0
# ---------------------------------------------------------------------------

printf "\n=== 2: passing Makefile fixture (stdlib unittest) ===\n"
ok_proj="$TMP/ok-proj"
mkdir -p "$ok_proj"
cat > "$ok_proj/Makefile" <<'EOF'
test:
	python3 -m unittest test_ok.py
.PHONY: test
EOF
cat > "$ok_proj/test_ok.py" <<'EOF'
import unittest

class TestTrivial(unittest.TestCase):
    def test_passing(self): self.assertEqual(1 + 1, 2)

if __name__ == "__main__":
    unittest.main()
EOF

set +e
python3 "$RUNNER" --project "$ok_proj" --steps test --no-write >/tmp/ok.out 2>&1
rc=$?
set -e
if [[ "$rc" == "0" ]]; then
  ok "passing tests -> exit 0"
else
  ko "passing tests exit=$rc; output: $(tail -5 /tmp/ok.out)"
fi

# ---------------------------------------------------------------------------
# 3. Failing test -> exit 1 + state written
# ---------------------------------------------------------------------------

printf "\n=== 3: failing python fixture writes state ===\n"
fail_proj="$TMP/fail-proj"
mkdir -p "$fail_proj" "$fail_proj/.cursor/hooks/.state"
cat > "$fail_proj/Makefile" <<'EOF'
test:
	python3 -m unittest test_fail.py
.PHONY: test
EOF
cat > "$fail_proj/test_fail.py" <<'EOF'
import unittest

class TestFailing(unittest.TestCase):
    def test_fails(self): self.assertEqual(1, 2)

if __name__ == "__main__":
    unittest.main()
EOF
# Pre-create state file so run_regression has somewhere to write.
echo '{"session_id":"1","task_seq":0}' > "$fail_proj/.cursor/hooks/.state/session.json"

set +e
AGENT_PACK_HOOKS_STATE="$fail_proj/.cursor/hooks/.state/session.json" \
  python3 "$RUNNER" --project "$fail_proj" --steps test >/tmp/fail.out 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "failing test -> exit 1" || ko "failing test exit=$rc"

last_ok="$(python3 -c "
import json
print(json.load(open('$fail_proj/.cursor/hooks/.state/session.json')).get('last_regression_ok'))
")"
[[ "$last_ok" == "False" ]] && ok "state records last_regression_ok=False" || ko "state last_regression_ok=$last_ok"

# ---------------------------------------------------------------------------
# 4. --json output
# ---------------------------------------------------------------------------

printf "\n=== 4: --json shape ===\n"
json_out="$(python3 "$RUNNER" --project "$ok_proj" --steps test --no-write --json 2>&1)"
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['ok'] is True
assert 'results' in d and 'plan' in d
"; then
  ok "JSON payload parses"
else
  ko "JSON malformed"
fi

# ---------------------------------------------------------------------------
# 5+6. Gate integration: require_regression_passed
# ---------------------------------------------------------------------------

printf "\n=== 5: gate demands regression when enabled ===\n"
gp="$TMP/gate-proj"
mkdir -p "$gp/.cursor/hooks/scripts"
mkdir -p "$gp/.cursor/hooks/.state"
mkdir -p "$gp/.cursor/memory"
cp "$PACK/hooks/scripts/"*.sh "$gp/.cursor/hooks/scripts/"
chmod +x "$gp/.cursor/hooks/scripts/"*.sh
cp "$PACK/memory/memory.py" "$PACK/memory/validate.py" "$gp/.cursor/memory/"
cp "$PACK/memory/session-handoff.md" "$PACK/memory/decisions.md" "$PACK/memory/patterns.md" \
   "$gp/.cursor/memory/"
# Config: turn on the regression gate.
echo '{"require_regression_passed": true}' > "$gp/.cursor/hooks/config.json"

export AGENT_PACK_HOOKS_STATE="$gp/.cursor/hooks/.state/session.json"
export AGENT_PACK_MEMORY_CLI="$gp/.cursor/memory/memory.py"
export AGENT_PACK_TELEMETRY_DIR="$gp/.cursor/telemetry"

# Seed, pretend a code file was written, invoke qa-verifier, append memory.
printf '%s' '{}' | bash "$gp/.cursor/hooks/scripts/seed-session.sh" >/dev/null
printf '%s' '{"file_path":"src/app.py"}' | bash "$gp/.cursor/hooks/scripts/record-write.sh" >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\nverify"}' \
  | bash "$gp/.cursor/hooks/scripts/record-subagent-start.sh" >/dev/null
printf '%s' '{"subagent_type":"generalPurpose"}' \
  | bash "$gp/.cursor/hooks/scripts/record-subagent-stop.sh" >/dev/null
cid="$(python3 "$gp/.cursor/memory/memory.py" current-id)"
python3 "$gp/.cursor/memory/memory.py" append --file session-handoff \
    --kind state --status in_progress \
    --summary "bootstrap-gate-test" \
    --body "seed entry so session-handoff correlation_id matches the active task" \
    >/dev/null
# memory.py writes the file but doesn't fire afterFileEdit in this test harness;
# trigger record-write manually so the hook state reflects a real session.
printf '%s' "{\"file_path\":\"$gp/.cursor/memory/session-handoff.md\"}" \
  | bash "$gp/.cursor/hooks/scripts/record-write.sh" >/dev/null

# Now simulate stop.
verdict_raw="$(printf '%s' '{}' | bash "$gp/.cursor/hooks/scripts/gate-stop.sh" 2>&1 | tail -1)"
if printf '%s' "$verdict_raw" | /usr/bin/grep -q "followup_message"; then
  printf '%s' "$verdict_raw" | /usr/bin/grep -q "regression" && \
    ok "gate emits followup mentioning regression" || \
    ko "followup did not mention regression"
else
  ko "gate allowed without regression: $verdict_raw"
fi

printf "\n=== 6: after successful regression run, gate allows ===\n"
# Write a minimal pyproject + passing test so run_regression has work to do,
# then run it targeting this gate project's state file.
cat > "$gp/Makefile" <<'EOF'
test:
	python3 -m unittest test_gate.py
.PHONY: test
EOF
cat > "$gp/test_gate.py" <<'EOF'
import unittest

class T(unittest.TestCase):
    def test_ok(self): self.assertTrue(True)

if __name__ == "__main__":
    unittest.main()
EOF

python3 "$RUNNER" --project "$gp" --steps test >/dev/null 2>&1 || true

verdict_raw2="$(printf '%s' '{}' | bash "$gp/.cursor/hooks/scripts/gate-stop.sh" 2>&1 | tail -1)"
if printf '%s' "$verdict_raw2" | /usr/bin/grep -q "followup_message"; then
  ko "gate still blocks after regression passed: $verdict_raw2"
else
  ok "gate allows after successful regression"
fi

unset AGENT_PACK_HOOKS_STATE AGENT_PACK_MEMORY_CLI AGENT_PACK_TELEMETRY_DIR

# ---------------------------------------------------------------------------
# 7. --retry: flaky step passes on a later attempt
# ---------------------------------------------------------------------------

printf "\n=== 7: --retry lets a flaky step pass ===\n"
flaky_proj="$TMP/flaky-proj"
mkdir -p "$flaky_proj"
# Makefile fails on the first invocation (creates sentinel) and passes
# on the second.
cat > "$flaky_proj/Makefile" <<'EOF'
test:
	@if [ -f .flaky-sentinel ]; then \
	   echo "pass on retry"; \
	else \
	   touch .flaky-sentinel; \
	   echo "fail on first attempt" >&2; \
	   exit 1; \
	fi
.PHONY: test
EOF

set +e
out="$(python3 "$RUNNER" --project "$flaky_proj" --steps test --retry 2 --no-write 2>&1)"
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "flaky step eventually passes with --retry 2" || ko "exit=$rc: $out"
printf '%s' "$out" | /usr/bin/grep -q "FLAKY" && ok "output marks step as FLAKY" || ko "no FLAKY marker"

# With retry=0 the same step should fail (reset sentinel).
rm -f "$flaky_proj/.flaky-sentinel"
set +e
python3 "$RUNNER" --project "$flaky_proj" --steps test --retry 0 --no-write >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "same step fails with --retry 0" || ko "--retry 0 exit=$rc"

# ---------------------------------------------------------------------------
# 8. --retry does NOT re-run timeouts
# ---------------------------------------------------------------------------

printf "\n=== 8: --retry does not re-run on timeout ===\n"
slow_proj="$TMP/slow-proj"
mkdir -p "$slow_proj"
cat > "$slow_proj/Makefile" <<'EOF'
test:
	sleep 4
.PHONY: test
EOF
set +e
start_ts=$(date +%s)
python3 "$RUNNER" --project "$slow_proj" --steps test --timeout 1 --retry 5 --no-write >/dev/null 2>&1
rc=$?
end_ts=$(date +%s)
set -e
elapsed=$((end_ts - start_ts))
[[ "$rc" == "1" ]] && ok "timeout propagates exit 1" || ko "exit=$rc"
# Timeouts MUST not retry. retry=5 with a 1s timeout would be 5+ seconds.
[[ "$elapsed" -lt "5" ]] && ok "timeout not retried (elapsed=${elapsed}s)" || \
  ko "timeout was retried (elapsed=${elapsed}s)"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
