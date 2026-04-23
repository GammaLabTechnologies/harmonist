#!/usr/bin/env bash
# Tests for local telemetry + report_usage.py.
#
# Builds a fully integrated fixture, simulates a mix of hook invocations
# (20 subagent calls across 4 slugs + 3 sessions + 2 followups + 1 protocol
# skip), then checks that the counter file + report reflect reality.

set -euo pipefail

unset AGENT_PACK_HOOKS_STATE AGENT_PACK_MEMORY_CLI 2>/dev/null || true

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE="$PACK/agents/scripts/upgrade.py"
REPORT="$PACK/agents/scripts/report_usage.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

proj="$TMP/proj"
mkdir -p "$proj"
cp "$PACK/AGENTS.md" "$proj/AGENTS.md"
python3 "$UPGRADE" --project "$proj" --pack "$PACK" --apply >/dev/null || true

# The upgrade installed all pack-owned files, including hooks + memory.
# Confirm we have a telemetry-clean starting point.
[[ -d "$proj/.cursor/hooks" ]] || { echo "fatal: upgrade did not install hooks"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Simulate 3 sessions.
# ---------------------------------------------------------------------------

printf "\n=== 1: seed-session fires the session counter ===\n"
for i in 1 2 3; do
  printf '%s' '{}' | bash "$proj/.cursor/hooks/scripts/seed-session.sh" >/dev/null
done
tel="$proj/.cursor/telemetry/agent-usage.json"
[[ -f "$tel" ]] && ok "telemetry file created" || ko "telemetry file missing"
sessions="$(python3 -c "import json;print(json.load(open('$tel'))['summaries'].get('sessions', 0))")"
[[ "$sessions" == "3" ]] && ok "sessions counter = 3" || ko "sessions counter = $sessions"

# ---------------------------------------------------------------------------
# 2. Simulate 20 subagent calls across 4 slugs.
# ---------------------------------------------------------------------------

printf "\n=== 2: subagent-start bumps per-slug counter ===\n"
# Reset state once to start clean.
rm -rf "$proj/.cursor/hooks/.state"
printf '%s' '{}' | bash "$proj/.cursor/hooks/scripts/seed-session.sh" >/dev/null

slugs=(qa-verifier qa-verifier qa-verifier qa-verifier qa-verifier \
       qa-verifier qa-verifier qa-verifier qa-verifier qa-verifier \
       security-reviewer security-reviewer security-reviewer security-reviewer \
       code-quality-auditor code-quality-auditor code-quality-auditor \
       repo-scout repo-scout repo-scout)
for s in "${slugs[@]}"; do
  printf '%s' "{\"subagent_type\":\"generalPurpose\",\"prompt\":\"AGENT: $s\\nverify\"}" \
    | bash "$proj/.cursor/hooks/scripts/record-subagent-start.sh" >/dev/null
done

get_count() {
  python3 -c "
import json
d = json.load(open('$tel'))
print((d.get('agents') or {}).get('$1', {}).get('invocations', 0))
"
}

[[ "$(get_count qa-verifier)"          == "10" ]] && ok "qa-verifier count = 10"          || ko "qa-verifier = $(get_count qa-verifier)"
[[ "$(get_count security-reviewer)"    == "4"  ]] && ok "security-reviewer count = 4"     || ko "security = $(get_count security-reviewer)"
[[ "$(get_count code-quality-auditor)" == "3"  ]] && ok "code-quality-auditor count = 3"  || ko "code = $(get_count code-quality-auditor)"
[[ "$(get_count repo-scout)"           == "3"  ]] && ok "repo-scout count = 3"            || ko "repo-scout = $(get_count repo-scout)"

# ---------------------------------------------------------------------------
# 3. Gate followups and skips bump summary counters.
# ---------------------------------------------------------------------------

printf "\n=== 3: stop-gate bumps followup/skip counters ===\n"
# Force a gate-bite: write happened but no reviewer + no handoff.
rm -rf "$proj/.cursor/hooks/.state"
printf '%s' '{}' | bash "$proj/.cursor/hooks/scripts/seed-session.sh" >/dev/null
printf '%s' '{"file_path":"/tmp/asp-telemetry-sentinel.py"}' | bash "$proj/.cursor/hooks/scripts/record-write.sh" >/dev/null
printf '%s' '{}' | bash "$proj/.cursor/hooks/scripts/gate-stop.sh" >/dev/null
followups="$(python3 -c "import json;print(json.load(open('$tel'))['summaries'].get('gate_followups', 0))")"
[[ "$followups" -ge "1" ]] && ok "gate_followups incremented" || ko "gate_followups = $followups"

# Force a PROTOCOL-SKIP path.
rm -rf "$proj/.cursor/hooks/.state"
printf '%s' '{}' | bash "$proj/.cursor/hooks/scripts/seed-session.sh" >/dev/null
printf '%s' '{"file_path":"/tmp/asp-telemetry-sentinel-2.py"}' | bash "$proj/.cursor/hooks/scripts/record-write.sh" >/dev/null
printf '%s' '{"response":"tiny fix.\nPROTOCOL-SKIP: nothing to verify"}' | bash "$proj/.cursor/hooks/scripts/gate-stop.sh" >/dev/null
skips="$(python3 -c "import json;print(json.load(open('$tel'))['summaries'].get('protocol_skips', 0))")"
[[ "$skips" -ge "1" ]] && ok "protocol_skips incremented" || ko "protocol_skips = $skips"

# ---------------------------------------------------------------------------
# 4. report_usage.py renders a human report with the expected data.
# ---------------------------------------------------------------------------

printf "\n=== 4: report_usage.py output ===\n"
out="$(python3 "$REPORT" --project "$proj" 2>&1)"
printf '%s' "$out" | grep -qF "qa-verifier" && ok "report lists qa-verifier" || ko "qa-verifier missing in report"
printf '%s' "$out" | grep -qF "sessions seen" && ok "report shows session count" || ko "no session-count line"
printf '%s' "$out" | grep -qF "PROTOCOL-SKIP opt-outs" && ok "report mentions protocol-skip" || ko "skip line missing"
printf '%s' "$out" | grep -qF "followup_message" && ok "report mentions followups" || ko "followup line missing"

# Expected: qa-verifier/security-reviewer/code-quality-auditor/repo-scout
# were invoked; the other strict agents (bg-regression-runner,
# sre-observability) were installed by upgrade.py but not invoked in this
# run, so the dead-balance section SHOULD list them. However when the
# user passes --recommend-removal, the rm plan must skip them.
printf '%s' "$out" | grep -qF "Installed but NEVER invoked" && \
  ok "dead-balance section lists uninvoked installed agents" || \
  ko "no dead-balance section"
printf '%s' "$out" | grep -qE "(bg-regression-runner|sre-observability)" && \
  ok "dead-balance names a strict uninvoked agent" || \
  ko "dead-balance content wrong"

# ---------------------------------------------------------------------------
# 5. Install an unused specialist + re-check dead-balance.
# ---------------------------------------------------------------------------

printf "\n=== 5: dead-balance surfaces when an uninvoked agent sits in .cursor/agents/ ===\n"
cp "$PACK/agents/engineering/engineering-solidity-smart-contract-engineer.md" \
   "$proj/.cursor/agents/"
out="$(python3 "$REPORT" --project "$proj" --recommend-removal 2>&1)"
printf '%s' "$out" | grep -qF "engineering-solidity-smart-contract-engineer" && \
  ok "solidity agent flagged as dead balance" || \
  ko "solidity agent not flagged"
printf '%s' "$out" | grep -qF "rm '.cursor/agents/engineering-solidity-smart-contract-engineer.md'" && \
  ok "recommend-removal prints rm command" || ko "rm command missing"
# Strict agents MUST NOT appear in the rm plan, even if never invoked.
if printf '%s' "$out" | grep -qE "rm '.cursor/agents/(qa-verifier|security-reviewer|code-quality-auditor|sre-observability|repo-scout|bg-regression-runner)\.md'"; then
  ko "strict agent recommended for removal (BUG)"
else
  ok "strict agents never in rm plan"
fi

# ---------------------------------------------------------------------------
# 6. --json mode parses.
# ---------------------------------------------------------------------------

printf "\n=== 6: JSON output ===\n"
json_out="$(python3 "$REPORT" --project "$proj" --json 2>&1)"
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'agents' in d and 'summaries' in d
assert d['agents']['qa-verifier']['invocations'] >= 10
assert 'engineering-solidity-smart-contract-engineer' in d['dead_balance']
" >/dev/null 2>&1; then
  ok "JSON payload parses with agents/summaries/dead_balance"
else
  ko "JSON payload invalid"
fi

# ---------------------------------------------------------------------------
# 7. Opt-out disables collection.
# ---------------------------------------------------------------------------

printf "\n=== 7: telemetry_enabled=false disables writes ===\n"
proj2="$TMP/proj2"
mkdir -p "$proj2"
cp "$PACK/AGENTS.md" "$proj2/AGENTS.md"
python3 "$UPGRADE" --project "$proj2" --pack "$PACK" --apply >/dev/null || true
mkdir -p "$proj2/.cursor/hooks"
echo '{"telemetry_enabled": false}' > "$proj2/.cursor/hooks/config.json"
for i in 1 2 3; do
  printf '%s' '{}' | bash "$proj2/.cursor/hooks/scripts/seed-session.sh" >/dev/null
done
[[ ! -f "$proj2/.cursor/telemetry/agent-usage.json" ]] && ok "telemetry file NOT created when opt-out" || ko "telemetry file exists despite opt-out"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
