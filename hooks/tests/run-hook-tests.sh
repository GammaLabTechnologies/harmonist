#!/usr/bin/env bash
# Integration tests for the enforcement hooks.
#
# Simulates Cursor hook invocations by piping synthetic JSON into each
# script and asserting the resulting state / output. No Cursor required.
#
# Tests use an isolated tmp directory for the memory CLI so no real
# memory files are touched. AGENT_PACK_MEMORY_CLI env override points
# gate-stop.sh at our test copy.

set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$HOOKS_DIR/scripts"
STATE_DIR="$HOOKS_DIR/.state"
STATE_FILE="$STATE_DIR/session.json"
PACK_MEMORY="$(cd "$HOOKS_DIR/.." && pwd)/memory"

# Per-run isolated memory directory — copy the real templates + CLI into a
# tmp dir and point the hooks at it. Teardown on exit.
TMP_MEMORY="$(mktemp -d)"
cp "$PACK_MEMORY/memory.py" "$PACK_MEMORY/validate.py" "$TMP_MEMORY/"
cp "$PACK_MEMORY/session-handoff.md" "$PACK_MEMORY/decisions.md" "$PACK_MEMORY/patterns.md" "$TMP_MEMORY/"
export AGENT_PACK_MEMORY_CLI="$TMP_MEMORY/memory.py"
export AGENT_PACK_HOOKS_STATE="$STATE_FILE"
export AGENT_PACK_TELEMETRY_DIR="$TMP_MEMORY/telemetry"

cleanup() {
  rm -rf "$TMP_MEMORY"
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

pass=0
fail=0
fail_list=()

reset_state() {
  rm -rf "$STATE_DIR"
  # Also wipe any memory state written by previous test so CLI re-reads
  # the fresh hooks state file.
  cp "$PACK_MEMORY/session-handoff.md" "$TMP_MEMORY/session-handoff.md"
  cp "$PACK_MEMORY/decisions.md" "$TMP_MEMORY/decisions.md"
  cp "$PACK_MEMORY/patterns.md" "$TMP_MEMORY/patterns.md"
}

assert() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf "  ok    %s\n" "$label"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s\n    expected: %s\n    actual:   %s\n" "$label" "$expected" "$actual"
    fail=$((fail + 1))
    fail_list+=("$label")
  fi
}

gate_verdict() {
  local input="$1"
  # Bash parameter expansion default '${1:-{}}' would *append* a spurious
  # closing brace to $1 due to nested-brace parsing; use an explicit guard.
  [[ -z "$input" ]] && input='{}'
  local out
  out="$(printf '%s' "$input" | bash "$SCRIPTS/gate-stop.sh")"
  if printf '%s' "$out" | grep -q '"followup_message"'; then
    echo "followup"
  else
    echo "allow"
  fi
}

pipe_json() {
  local json="$1" script="$2"
  printf '%s' "$json" | bash "$script"
}

# Append a valid state entry to session-handoff.md in the test memory,
# using the active_correlation_id from the hooks state file.
append_handoff() {
  python3 "$TMP_MEMORY/memory.py" append \
    --file session-handoff \
    --kind state --status done \
    --summary "Test entry: $*" \
    --body "## Changes
- $*

## Open issues
- none" >/dev/null
}

active_cid() {
  python3 -c 'import json,sys;print(json.load(open("'$STATE_FILE'")).get("active_correlation_id",""))'
}

# ---------------------------------------------------------------------------

printf "\n=== scenario 1: no writes, pure Q&A — allow ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
assert "stop-verdict after no writes" "allow" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 2: write happened, no reviewer — followup ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict after bare write" "followup" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 3: write + qa-verifier + handoff with matching cid — allow ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
append_handoff "scenario 3 change"
pipe_json "{\"file_path\":\"$TMP_MEMORY/session-handoff.md\"}" "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict after full protocol" "allow" "$(gate_verdict '{}')"

# Verify bump happened — task_seq incremented
post_cid="$(active_cid)"
if [[ "$post_cid" != "" && "$post_cid" == *"-1" ]]; then
  printf "  ok    task_seq advanced after successful stop (active_cid=%s)\n" "$post_cid"
  pass=$((pass + 1))
else
  printf "  FAIL  task_seq advance check (active_cid=%s, expected *-1)\n" "$post_cid"
  fail=$((fail + 1))
  fail_list+=("task_seq advance after successful stop")
fi

# ---------------------------------------------------------------------------

printf "\n=== scenario 4: write + reviewer but NO handoff entry — followup ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
# note: no append_handoff here
assert "stop-verdict after qa-verifier but missing handoff" "followup" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 5: write + qa-verifier + handoff touched but no cid entry — followup ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
# Touch the handoff file but DON'T append a proper entry with active cid
echo "" >> "$TMP_MEMORY/session-handoff.md"
pipe_json "{\"file_path\":\"$TMP_MEMORY/session-handoff.md\"}" "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict when handoff has no matching correlation_id" "followup" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 6: PROTOCOL-SKIP marker — allow ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
skip_input='{"response":"Tiny comment tweak.\\nPROTOCOL-SKIP: comment-only, no behaviour change"}'
assert "stop-verdict with PROTOCOL-SKIP" "allow" "$(gate_verdict "$skip_input")"

# ---------------------------------------------------------------------------

printf "\n=== scenario 7: node_modules write ignored — allow ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"node_modules/lib/index.js"}' "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict after only-ignored writes" "allow" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 8: reviewer missing AGENT: marker — followup ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"please run qa verification on the diff"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
assert "stop-verdict when reviewer lacked AGENT: marker" "followup" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 9: memory CLI invalid entry — followup ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/foo.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
# Inject a malformed entry directly (bypass CLI) to simulate hand-editing mistake
cat >> "$TMP_MEMORY/session-handoff.md" <<EOF

<!-- memory-entry:start -->
---
id: broken-entry
correlation_id: not-a-number
at: yesterday
kind: decision
status: maybe
author: someone
summary: this is wrong on multiple axes
---

Some body.

<!-- memory-entry:end -->
EOF
pipe_json "{\"file_path\":\"$TMP_MEMORY/session-handoff.md\"}" "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict when handoff has invalid entries" "followup" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------

printf "\n=== scenario 10: lightweight-mode — all-trivial writes auto-allow ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
# Only docs / README / CHANGELOG / .gitignore touched -- no reviewer needed.
pipe_json '{"file_path":"README.md"}'          "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"file_path":"CHANGELOG.md"}'       "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"file_path":"docs/intro.md"}'      "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"file_path":".gitignore"}'         "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict trivial-only writes" "allow" "$(gate_verdict '{}')"

printf "\n=== scenario 11: lightweight-mode — mixed writes still need reviewer ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
# README is trivial, src/app.ts is not -- ANY non-trivial write defeats fast path.
pipe_json '{"file_path":"README.md"}'          "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"file_path":"src/app.ts"}'         "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict mixed writes" "followup" "$(gate_verdict '{}')"

printf "\n=== scenario 12: lightweight-mode — opt-out forces reviewer even for docs ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
# Temporarily override config to disable the fast path.
echo '{"allow_trivial_without_review": false}' > "$HOOKS_DIR/config.json"
pipe_json '{"file_path":"README.md"}'          "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict trivial writes with opt-out" "followup" "$(gate_verdict '{}')"
rm -f "$HOOKS_DIR/config.json"

printf "\n=== scenario 13: capability scoping — readonly subagent writing is blocked ===\n"
reset_state
# Seed a fake readonly agent file under .cursor/agents/ for the scoping
# lookup to find. record-subagent-start.sh reads `./.cursor/agents/<slug>.md`
# relative to CWD, so run the hook in a tmp CWD that contains the fixture.
readonly_fixture="$TMP_MEMORY/readonly-sandbox"
mkdir -p "$readonly_fixture/.cursor/agents"
cat > "$readonly_fixture/.cursor/agents/evil-reviewer.md" <<EOF
---
schema_version: 2
name: evil-reviewer
description: pretends to be readonly but a buggy implementation lets it write
category: review
protocol: strict
readonly: true
is_background: false
model: inherit
tags: [review]
domains: [all]
---

Body content.
EOF
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
(cd "$readonly_fixture" && \
  printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: evil-reviewer\nreview"}' \
    | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null)
# Now simulate a write while evil-reviewer is mid-invocation.
pipe_json '{"file_path":"src/app.py"}' "$SCRIPTS/record-write.sh" >/dev/null
assert "stop-verdict readonly violation blocks" "followup" "$(gate_verdict '{}')"

printf "\n=== scenario 14: non-readonly subagent writing is fine ===\n"
reset_state
cat > "$readonly_fixture/.cursor/agents/normal-writer.md" <<EOF
---
schema_version: 2
name: normal-writer
description: not readonly
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [backend]
domains: [all]
---

Body.
EOF
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
(cd "$readonly_fixture" && \
  printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: normal-writer\nbuild"}' \
    | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null)
# Write while normal-writer is active -- no violation.
pipe_json '{"file_path":"src/app.py"}' "$SCRIPTS/record-write.sh" >/dev/null
# Reviewer + memory still required; the point is that no readonly violation
# was added. Query state directly.
viol_count="$(cat "$STATE_FILE" | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(len(d.get("readonly_violations") or []))')"
if [[ "$viol_count" == "0" ]]; then
  printf "  ok    writes by non-readonly agent record no violation\n"
  pass=$((pass + 1))
else
  printf "  FAIL  violations=%s when writer is non-readonly\n" "$viol_count"
  fail=$((fail + 1))
fi

printf "\n=== scenario 15: PROTOCOL-SKIP abuse warning appears in session bootstrap ===\n"
reset_state
# Pre-populate the telemetry file with a skip-heavy history and confirm
# the sessionStart injection surfaces the warning so the user sees it.
mkdir -p "$TMP_MEMORY/telemetry"
cat > "$TMP_MEMORY/telemetry/agent-usage.json" <<'JSON'
{"summaries": {"sessions": 20, "protocol_skips": 8, "gate_allow_satisfied": 15}}
JSON
ctx_out="$(pipe_json '{}' "$SCRIPTS/seed-session.sh")"
if printf '%s' "$ctx_out" | python3 -c 'import json,sys
d=json.load(sys.stdin)
sys.exit(0 if "PROTOCOL-SKIP audit" in d.get("additional_context","") else 1)'; then
  printf "  ok    sessionStart warns on PROTOCOL-SKIP abuse\n"
  pass=$((pass + 1))
else
  printf "  FAIL  expected PROTOCOL-SKIP audit warning in additional_context\n"
  fail=$((fail + 1))
  fail_list+=("protocol-skip-warn emitted")
fi

reset_state
# Below threshold: 2 skips / (2+18)=10% -- must NOT warn.
cat > "$TMP_MEMORY/telemetry/agent-usage.json" <<'JSON'
{"summaries": {"sessions": 20, "protocol_skips": 2, "gate_allow_satisfied": 18}}
JSON
ctx_out="$(pipe_json '{}' "$SCRIPTS/seed-session.sh")"
if printf '%s' "$ctx_out" | python3 -c 'import json,sys
d=json.load(sys.stdin)
sys.exit(1 if "PROTOCOL-SKIP audit" in d.get("additional_context","") else 0)'; then
  printf "  ok    sessionStart is quiet when PROTOCOL-SKIP ratio is low\n"
  pass=$((pass + 1))
else
  printf "  FAIL  unexpected PROTOCOL-SKIP warning below threshold\n"
  fail=$((fail + 1))
  fail_list+=("protocol-skip-warn quiet")
fi
rm -f "$TMP_MEMORY/telemetry/agent-usage.json"

printf "\n=== scenario 16: fail-closed at loop_limit exhaustion ===\n"
# After 2 prior followups, attempt #3 must NOT silently allow -- it must
# emit_exhausted: write an incidents.json entry, bump task_seq (so the
# next task is not permanently stuck), and still emit a final followup
# message flagged as EXHAUSTED.
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
active_cid="$(python3 -c 'import json;print(json.load(open("'"$STATE_FILE"'")).get("active_correlation_id",""))')"
# Simulate two prior failed attempts + a code write, no reviewer, no handoff.
python3 -c "
import json,pathlib
p = pathlib.Path('$STATE_FILE')
d = json.loads(p.read_text())
d['writes'] = [{'path':'src/app.py'}]
d['enforcement_attempts'] = 2  # next attempt = 3 = loop_limit
p.write_text(json.dumps(d, indent=2))
"
out="$(printf '{}' | bash "$SCRIPTS/gate-stop.sh")"
if printf '%s' "$out" | grep -q 'EXHAUSTED'; then
  printf "  ok    gate-stop emits EXHAUSTED at attempt == loop_limit\n"
  pass=$((pass + 1))
else
  printf "  FAIL  gate-stop did not emit EXHAUSTED (got %s)\n" "$out"
  fail=$((fail + 1))
  fail_list+=("exhausted emit")
fi
if [[ -f "$STATE_DIR/incidents.json" ]]; then
  inc_count="$(python3 -c 'import json;print(len(json.load(open("'"$STATE_DIR/incidents.json"'"))["incidents"]))')"
  if [[ "$inc_count" -ge 1 ]]; then
    printf "  ok    incidents.json has %s record(s)\n" "$inc_count"
    pass=$((pass + 1))
  else
    printf "  FAIL  incidents.json empty\n"; fail=$((fail + 1))
  fi
else
  printf "  FAIL  incidents.json was not written\n"; fail=$((fail + 1))
fi
# task_seq must have advanced even though protocol was violated.
post_cid="$(python3 -c 'import json;print(json.load(open("'"$STATE_FILE"'")).get("active_correlation_id",""))')"
if [[ "$post_cid" != "$active_cid" ]]; then
  printf "  ok    task_seq advanced after exhausted (cid changed)\n"
  pass=$((pass + 1))
else
  printf "  FAIL  task_seq did not advance (cid still %s)\n" "$post_cid"
  fail=$((fail + 1))
fi
# Next sessionStart surfaces the incident to the user.
ctx_out="$(pipe_json '{}' "$SCRIPTS/seed-session.sh")"
if printf '%s' "$ctx_out" | python3 -c 'import json,sys
d=json.load(sys.stdin)
sys.exit(0 if "PROTOCOL-EXHAUSTED" in d.get("additional_context","") else 1)'; then
  printf "  ok    next sessionStart surfaces protocol-exhausted banner\n"
  pass=$((pass + 1))
else
  printf "  FAIL  no PROTOCOL-EXHAUSTED banner in next sessionStart\n"
  fail=$((fail + 1))
fi
rm -f "$STATE_DIR/incidents.json"

printf "\n=== scenario 17: .sh subagentStart accepts fallback AGENT markers ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"<!-- AGENT: qa-verifier -->\nhello"}' \
  | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null
slug="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
calls=d.get("subagent_calls",[])
print(calls[-1].get("slug") or "")')"
if [[ "$slug" == "qa-verifier" ]]; then
  printf "  ok    .sh accepts <!-- AGENT: ... --> fallback\n"
  pass=$((pass + 1))
else
  printf "  FAIL  .sh fallback slug=%s (expected qa-verifier)\n" "$slug"
  fail=$((fail + 1))
fi
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"<agent>code-quality-auditor</agent>\nreview"}' \
  | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null
slug="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
calls=d.get("subagent_calls",[])
print(calls[-1].get("slug") or "")')"
if [[ "$slug" == "code-quality-auditor" ]]; then
  printf "  ok    .sh accepts <agent>…</agent> fallback\n"
  pass=$((pass + 1))
else
  printf "  FAIL  .sh xml fallback slug=%s\n" "$slug"
  fail=$((fail + 1))
fi

printf "\n=== scenario 18: hook_runner.py cross-platform parity ===\n"
# Windows-native Cursor has no bash. hook_runner.py is the pure-Python
# implementation; every .sh script has a Python twin. Verify that each
# phase of the runner produces a sensible response AND ends up with
# matching session state.
reset_state
# sessionStart
out_py="$(printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart)"
if printf '%s' "$out_py" | python3 -c 'import json,sys
d=json.load(sys.stdin)
sys.exit(0 if "additional_context" in d else 1)'; then
  printf "  ok    runner sessionStart emits additional_context\n"
  pass=$((pass + 1))
else
  printf "  FAIL  runner sessionStart output=%s\n" "$out_py"
  fail=$((fail + 1))
fi
# afterFileEdit
printf '{"file_path":"src/app.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
writes="$(python3 -c 'import json;print(len(json.load(open("'"$STATE_FILE"'"))["writes"]))')"
if [[ "$writes" == "1" ]]; then
  printf "  ok    runner afterFileEdit records write to session.json\n"
  pass=$((pass + 1))
else
  printf "  FAIL  runner afterFileEdit writes=%s (expected 1)\n" "$writes"
  fail=$((fail + 1))
fi
# afterFileEdit for ignored path -- no write recorded
printf '{"file_path":".git/HEAD"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
writes="$(python3 -c 'import json;print(len(json.load(open("'"$STATE_FILE"'"))["writes"]))')"
if [[ "$writes" == "1" ]]; then
  printf "  ok    runner afterFileEdit honours skip_path_patterns\n"
  pass=$((pass + 1))
else
  printf "  FAIL  runner afterFileEdit wrote to ignored path (writes=%s)\n" "$writes"
  fail=$((fail + 1))
fi
# subagentStart records the slug from AGENT: marker
printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\nreview"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null
# subagentStop credits the reviewer slug
printf '{}' | python3 "$SCRIPTS/hook_runner.py" subagentStop >/dev/null
seen="$(python3 -c 'import json;print(",".join(json.load(open("'"$STATE_FILE"'")).get("reviewers_seen",[])))')"
if [[ "$seen" == "qa-verifier" ]]; then
  printf "  ok    runner subagentStart+Stop credits reviewer\n"
  pass=$((pass + 1))
else
  printf "  FAIL  reviewers_seen=%s (expected qa-verifier)\n" "$seen"
  fail=$((fail + 1))
fi
# subagentStart accepts the HTML-comment fallback marker
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"<!-- AGENT: security-reviewer -->\nhello"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null
fallback_slug="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
calls=d.get("subagent_calls",[])
print(calls[-1]["slug"] if calls else "")')"
if [[ "$fallback_slug" == "security-reviewer" ]]; then
  printf "  ok    runner accepts <!-- AGENT: ... --> fallback marker\n"
  pass=$((pass + 1))
else
  printf "  FAIL  fallback marker: slug=%s (expected security-reviewer)\n" "$fallback_slug"
  fail=$((fail + 1))
fi
# stop with no writes allows
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
stop_out="$(printf '{}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$stop_out" | python3 -c 'import json,sys;d=json.load(sys.stdin);sys.exit(0 if d=={} else 1)'; then
  printf "  ok    runner stop allows when no writes\n"
  pass=$((pass + 1))
else
  printf "  FAIL  runner stop output=%s\n" "$stop_out"
  fail=$((fail + 1))
fi
# stop with writes but no reviewer -> followup
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":"src/api.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
stop_out="$(printf '{}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$stop_out" | python3 -c 'import json,sys;d=json.load(sys.stdin);sys.exit(0 if "followup_message" in d else 1)'; then
  printf "  ok    runner stop returns followup when reviewer missing\n"
  pass=$((pass + 1))
else
  printf "  FAIL  runner stop output=%s\n" "$stop_out"
  fail=$((fail + 1))
fi
rm -f "$STATE_DIR/incidents.json"

printf "\n=== summary ===\n  passed: %s\n  failed: %s\n" "$pass" "$fail"
if (( fail > 0 )); then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
