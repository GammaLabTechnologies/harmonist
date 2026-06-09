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
REPOMAP="$(cd "$HOOKS_DIR/.." && pwd)/agents/scripts/repomap.py"

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
  # Tests that exercise config overrides write a transient config.json into
  # the pack's hooks dir; make sure it never survives a run (even on abort).
  rm -f "$HOOKS_DIR/config.json"
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

# ---------------------------------------------------------------------------
printf "\n=== scenario 19: concurrent-subagent cap (default max 3) ===\n"
# .sh path: first 3 launches allowed, the 4th is denied.
reset_state
sa_out=""
for i in 1 2 3; do
  printf '%s' "{\"subagent_type\":\"generalPurpose\",\"prompt\":\"AGENT: a${i}\\nwork\"}" \
    | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null
done
sa_out="$(printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: a4\nwork"}' \
  | bash "$SCRIPTS/record-subagent-start.sh")"
if printf '%s' "$sa_out" | grep -q '"permission": *"deny"'; then
  printf "  ok    .sh denies the 4th concurrent subagent\n"; pass=$((pass + 1))
else
  printf "  FAIL  .sh did not deny 4th subagent: %s\n" "$sa_out"; fail=$((fail + 1)); fail_list+=("sh-cap-deny")
fi
# The 4th must NOT have been recorded (still 3 calls).
n_calls="$(python3 -c 'import json;print(len(json.load(open("'"$STATE_FILE"'")).get("subagent_calls",[])))')"
assert ".sh cap did not record the denied call" "3" "$n_calls"

# runner path: same -- 4th launch denied.
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
for i in 1 2 3; do
  printf '%s' "{\"subagent_type\":\"generalPurpose\",\"prompt\":\"AGENT: b${i}\\nwork\"}" \
    | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null
done
run_out="$(printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: b4\nwork"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart)"
if printf '%s' "$run_out" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("permission")=="deny" else 1)'; then
  printf "  ok    runner denies the 4th concurrent subagent\n"; pass=$((pass + 1))
else
  printf "  FAIL  runner did not deny 4th subagent: %s\n" "$run_out"; fail=$((fail + 1)); fail_list+=("runner-cap-deny")
fi
# After a subagentStop frees a slot, the next launch is allowed again.
printf '{}' | python3 "$SCRIPTS/hook_runner.py" subagentStop >/dev/null
allow_out="$(printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: b5\nwork"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart)"
if printf '%s' "$allow_out" | python3 -c 'import json,sys;d=json.load(sys.stdin);sys.exit(0 if d.get("permission")!="deny" else 1)'; then
  printf "  ok    runner allows again after a subagent stops\n"; pass=$((pass + 1))
else
  printf "  FAIL  runner still denied after a stop: %s\n" "$allow_out"; fail=$((fail + 1)); fail_list+=("runner-cap-recover")
fi
# Disabling the cap (max_concurrent_subagents=0) lets everything through.
reset_state
printf '{"max_concurrent_subagents":0}' > "$HOOKS_DIR/config.json"
for i in 1 2 3 4 5; do
  printf '%s' "{\"subagent_type\":\"generalPurpose\",\"prompt\":\"AGENT: c${i}\\nwork\"}" \
    | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null
done
n_uncapped="$(python3 -c 'import json;print(len(json.load(open("'"$STATE_FILE"'")).get("subagent_calls",[])))')"
rm -f "$HOOKS_DIR/config.json"
assert "cap=0 disables the limit (all 5 recorded)" "5" "$n_uncapped"

# ---------------------------------------------------------------------------
printf "\n=== scenario 20: impact-aware affected-tests gate ===\n"
reset_state
FIXA="$(mktemp -d)"
mkdir -p "$FIXA/src" "$FIXA/tests"
cat > "$FIXA/src/fee.py" <<'PYEOF'
def calc_fee(a):
    return a * 0.03
PYEOF
cat > "$FIXA/src/billing.py" <<'PYEOF'
from src.fee import calc_fee
def charge(x):
    return calc_fee(x)
PYEOF
cat > "$FIXA/tests/test_billing.py" <<'PYEOF'
from src.billing import charge
def test_charge():
    assert charge(100) == 3.0
PYEOF
python3 "$REPOMAP" build --project "$FIXA" >/dev/null
export AGENT_PACK_REPOMAP_CLI="$REPOMAP"
# Isolate the affected-tests gate: enable it, disable the others.
echo '{"require_affected_tests": true, "require_qa_verifier": false, "require_any_reviewer": false, "require_session_handoff_update": false}' > "$HOOKS_DIR/config.json"
(cd "$FIXA" && printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null)
(cd "$FIXA" && printf '{"file_path":"src/billing.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null)
gate_out="$(cd "$FIXA" && printf '{}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$gate_out" | grep -q "test_billing.py"; then
  printf "  ok    affected-tests gate blocks and names the impacted test\n"; pass=$((pass + 1))
else
  printf "  FAIL  affected-tests gate did not name impacted test: %s\n" "${gate_out:0:200}"; fail=$((fail + 1)); fail_list+=("affected-gate-block")
fi
# Gate OFF -> same edit is allowed (no other gates active).
reset_state
echo '{"require_affected_tests": false, "require_qa_verifier": false, "require_any_reviewer": false, "require_session_handoff_update": false}' > "$HOOKS_DIR/config.json"
(cd "$FIXA" && printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null)
(cd "$FIXA" && printf '{"file_path":"src/billing.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null)
gate_off="$(cd "$FIXA" && printf '{}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$gate_off" | python3 -c 'import json,sys;d=json.load(sys.stdin);sys.exit(0 if "followup_message" not in d else 1)'; then
  printf "  ok    gate off allows the same edit\n"; pass=$((pass + 1))
else
  printf "  FAIL  gate off still blocked: %s\n" "${gate_off:0:200}"; fail=$((fail + 1)); fail_list+=("affected-gate-off")
fi
rm -f "$HOOKS_DIR/config.json"
rm -rf "$FIXA"
unset AGENT_PACK_REPOMAP_CLI

# ---------------------------------------------------------------------------
printf "\n=== scenario 21: delegation-context gate (opt-in) ===\n"
# Enable the gate; disable the concurrency cap so it can't interfere.
reset_state
echo '{"require_delegation_context": true, "min_delegation_chars": 80, "max_concurrent_subagents": 0}' > "$HOOKS_DIR/config.json"
# Thin (marker-only-ish) delegation -> denied by the runner.
thin_out="$(printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\ncheck"}' | python3 "$SCRIPTS/hook_runner.py" subagentStart)"
if printf '%s' "$thin_out" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("permission")=="deny" else 1)'; then
  printf "  ok    runner denies a thin delegation\n"; pass=$((pass + 1))
else
  printf "  FAIL  runner allowed thin delegation: %s\n" "${thin_out:0:160}"; fail=$((fail + 1)); fail_list+=("deleg-thin")
fi
# Rich delegation with real handoff -> allowed.
reset_state
rich='{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\nPROJECT PRECEDENCE: payments module, no float for money.\nVerify the diff in src/api/auth.ts: new endpoints, edge cases, breaking-change risk, and that tests cover the error paths. Success = a done/blocked verdict with evidence."}'
rich_out="$(printf '%s' "$rich" | python3 "$SCRIPTS/hook_runner.py" subagentStart)"
if printf '%s' "$rich_out" | python3 -c 'import json,sys;d=json.load(sys.stdin);sys.exit(0 if d.get("permission")!="deny" else 1)'; then
  printf "  ok    runner allows a delegation with full handoff\n"; pass=$((pass + 1))
else
  printf "  FAIL  runner denied a rich delegation: %s\n" "${rich_out:0:160}"; fail=$((fail + 1)); fail_list+=("deleg-rich")
fi
# .sh path denies the thin delegation too (parity).
reset_state
sh_thin="$(printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\ncheck"}' | bash "$SCRIPTS/record-subagent-start.sh")"
if printf '%s' "$sh_thin" | grep -q '"permission": *"deny"'; then
  printf "  ok    .sh denies a thin delegation\n"; pass=$((pass + 1))
else
  printf "  FAIL  .sh allowed thin delegation: %s\n" "${sh_thin:0:160}"; fail=$((fail + 1)); fail_list+=("deleg-thin-sh")
fi
rm -f "$HOOKS_DIR/config.json"

# ---------------------------------------------------------------------------
printf "\n=== scenario 22: HITL gate on dangerous shell commands ===\n"
danger="$(printf '%s' '{"command":"rm -rf /"}' | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution)"
if printf '%s' "$danger" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("permission")=="ask" else 1)'; then
  printf "  ok    dangerous command -> ask\n"; pass=$((pass + 1))
else
  printf "  FAIL  dangerous command not gated: %s\n" "${danger:0:160}"; fail=$((fail + 1)); fail_list+=("hitl-danger")
fi
safe="$(printf '%s' '{"command":"npm test"}' | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution)"
if printf '%s' "$safe" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("permission")=="allow" else 1)'; then
  printf "  ok    routine command -> allow\n"; pass=$((pass + 1))
else
  printf "  FAIL  routine command gated: %s\n" "${safe:0:160}"; fail=$((fail + 1)); fail_list+=("hitl-safe")
fi
echo '{"hitl_enabled": false}' > "$HOOKS_DIR/config.json"
off="$(printf '%s' '{"command":"rm -rf /"}' | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution)"
if printf '%s' "$off" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("permission")=="allow" else 1)'; then
  printf "  ok    hitl_enabled=false disables the gate\n"; pass=$((pass + 1))
else
  printf "  FAIL  hitl off still gated: %s\n" "${off:0:160}"; fail=$((fail + 1)); fail_list+=("hitl-off")
fi
rm -f "$HOOKS_DIR/config.json"
# Empty payload (host sent nothing / stdin timed out): the gate cannot
# evaluate the command, so it must ASK -- allowing silently would fail
# open on exactly the events it exists for.
empty_perm="$(printf '{}' | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution \
  | python3 -c 'import json,sys;print(json.load(sys.stdin).get("permission",""))')"
assert "HITL asks on empty payload" "ask" "$empty_perm"
nostdin_perm="$(printf '' | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution \
  | python3 -c 'import json,sys;print(json.load(sys.stdin).get("permission",""))')"
assert "HITL asks on absent stdin payload" "ask" "$nostdin_perm"

# ---------------------------------------------------------------------------
printf "\n=== scenario 23: HITL gate catches hardened catastrophic forms ===\n"
# The old single-dash pattern missed these; the hardened patterns must ask.
for c in "rm -rf /usr" "rm --recursive --force /" "rm -rf --no-preserve-root /" "dd of=/dev/sda if=/dev/zero" "dd of=//dev/sda if=/dev/zero"; do
  perm="$(printf '%s' "{\"command\":\"$c\"}" | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution \
          | python3 -c 'import json,sys;print(json.load(sys.stdin).get("permission",""))')"
  assert "HITL asks for: $c" "ask" "$perm"
done
# Quoted catastrophic targets and lowercase SQL: the rm rule must tolerate
# quotes (`rm -rf "$HOME"`), and DROP/TRUNCATE must match case-insensitively.
# Built via json.dumps because the commands themselves contain double quotes.
for c in 'rm -rf "$HOME"' 'rm -rf "/"' 'drop table users'; do
  payload="$(C="$c" python3 -c 'import json,os;print(json.dumps({"command":os.environ["C"]}))')"
  perm="$(printf '%s' "$payload" | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution \
          | python3 -c 'import json,sys;print(json.load(sys.stdin).get("permission",""))')"
  assert "HITL asks for: $c" "ask" "$perm"
done
# Routine recursive deletes / test commands must NOT be gated -- and a dd
# that merely READS a device while writing a regular file (backup/restore)
# is routine, not a disk wipe (only of=/dev/... is gated).
for c in "rm -rf node_modules" "npm test" "rm notes.txt" "dd if=/dev/sda of=/tmp/backup.img"; do
  perm="$(printf '%s' "{\"command\":\"$c\"}" | python3 "$SCRIPTS/hook_runner.py" beforeShellExecution \
          | python3 -c 'import json,sys;print(json.load(sys.stdin).get("permission",""))')"
  assert "HITL allows: $c" "allow" "$perm"
done

# ---------------------------------------------------------------------------
printf "\n=== scenario 24: PROTOCOL-SKIP is scoped to the final message (no fail-open) ===\n"
# (a) A genuine skip in the agent's response is honoured.
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":"src/x.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
skip_out="$(printf '%s' '{"response":"tiny tweak.\nPROTOCOL-SKIP: comment-only"}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$skip_out" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin)=={} else 1)'; then
  printf "  ok    genuine PROTOCOL-SKIP in response allows\n"; pass=$((pass + 1))
else
  printf "  FAIL  genuine skip not honoured: %s\n" "${skip_out:0:120}"; fail=$((fail + 1)); fail_list+=("skip-honoured")
fi
# (b) The echoed seed/followup TEMPLATE in a context field must NOT skip.
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":"src/x.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
echo_out="$(printf '%s' '{"additional_context":"include PROTOCOL-SKIP: <one-line reason> in your final message"}' | python3 "$SCRIPTS/hook_runner.py" stop)"
if printf '%s' "$echo_out" | python3 -c 'import json,sys;sys.exit(0 if "followup_message" in json.load(sys.stdin) else 1)'; then
  printf "  ok    echoed template does NOT fail the gate open\n"; pass=$((pass + 1))
else
  printf "  FAIL  echoed template bypassed the gate: %s\n" "${echo_out:0:120}"; fail=$((fail + 1)); fail_list+=("skip-fail-open")
fi

# ---------------------------------------------------------------------------
printf "\n=== scenario 25: relative memory path is recorded (not skipped) ===\n"
# .cursor/ is in skip_path_patterns; a relative handoff path must still be
# tracked as a memory_update so the stop gate's handoff requirement is
# satisfiable on the Python path.
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":".cursor/memory/session-handoff.md"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
mem_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("memory_updates",[])))')"
wr_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("writes",[])))')"
assert "relative handoff recorded as memory_update" "1" "$mem_n"
assert "relative handoff NOT counted as a code write" "0" "$wr_n"

# ---------------------------------------------------------------------------
printf "\n=== scenario 26: memory classification requires the memory DIR, not just the basename ===\n"
# frontend/patterns.md shares a basename with a memory file but does NOT
# live in the memory dir: it must be recorded as a CODE write (reviewer
# required), not as a memory update silently bypassing the gate.
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":"frontend/patterns.md"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
mem_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("memory_updates",[])))')"
wr_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("writes",[])))')"
assert "runner: lookalike path is NOT a memory update" "0" "$mem_n"
assert "runner: lookalike path IS a code write" "1" "$wr_n"
# .sh parity.
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"frontend/patterns.md"}' "$SCRIPTS/record-write.sh" >/dev/null
mem_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("memory_updates",[])))')"
wr_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("writes",[])))')"
assert ".sh: lookalike path is NOT a memory update" "0" "$mem_n"
assert ".sh: lookalike path IS a code write" "1" "$wr_n"
# The real memory file (absolute path into the memory dir) still classifies.
pipe_json "{\"file_path\":\"$TMP_MEMORY/patterns.md\"}" "$SCRIPTS/record-write.sh" >/dev/null
mem_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("memory_updates",[])))')"
assert ".sh: real memory-dir path IS a memory update" "1" "$mem_n"

# ---------------------------------------------------------------------------
printf "\n=== scenario 27: handoff written via the memory CLI (no afterFileEdit) satisfies the gate ===\n"
# The documented write path is the CLI -- a shell command that never fires
# afterFileEdit. The gate must fall back to the handoff file on disk
# instead of looping to exhaustion.
runner_verdict() {
  local input="${1:-}"
  [[ -z "$input" ]] && input='{}'
  local out
  out="$(printf '%s' "$input" | python3 "$SCRIPTS/hook_runner.py" stop)"
  if printf '%s' "$out" | grep -q '"followup_message"'; then
    echo "followup"
  else
    echo "allow"
  fi
}
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
printf '{"file_path":"src/api/auth.ts"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\nverify"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null
printf '{}' | python3 "$SCRIPTS/hook_runner.py" subagentStop >/dev/null
append_handoff "scenario 27 CLI-only handoff (runner)"
assert "runner: CLI-only handoff satisfies the stop gate" "allow" "$(runner_verdict '{}')"
# .sh parity.
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
append_handoff "scenario 27 CLI-only handoff (.sh)"
assert ".sh: CLI-only handoff satisfies the stop gate" "allow" "$(gate_verdict '{}')"

# ---------------------------------------------------------------------------
printf "\n=== scenario 28: correlation-id match is line-anchored (…-1 must not match …-10) ===\n"
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
# Force the active task to <sid>-1 while the handoff only has <sid>-10.
sid="$(python3 -c 'import json;print(json.load(open("'"$STATE_FILE"'"))["session_id"])')"
python3 - "$STATE_FILE" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
d = json.loads(p.read_text())
d["task_seq"] = 1
d["active_correlation_id"] = f"{d['session_id']}-1"
p.write_text(json.dumps(d, indent=2))
PYEOF
cat >> "$TMP_MEMORY/session-handoff.md" <<EOF

<!-- memory-entry:start -->
---
id: ${sid}-10-state
correlation_id: ${sid}-10
at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
kind: state
status: done
author: orchestrator
summary: entry for a DIFFERENT task whose cid merely extends the active one
---

This entry belongs to task ${sid}-10, not ${sid}-1; the gate must not accept it.

<!-- memory-entry:end -->
EOF
pipe_json "{\"file_path\":\"$TMP_MEMORY/session-handoff.md\"}" "$SCRIPTS/record-write.sh" >/dev/null
assert ".sh: …-10 entry does not satisfy …-1 (anchored match)" "followup" "$(gate_verdict '{}')"
assert "runner: …-10 entry does not satisfy …-1 (anchored match)" "followup" "$(runner_verdict '{}')"

printf "\n=== scenario 29: late stop of a background readonly reviewer survives the task bump ===\n"
# The DOCUMENTED happy path: bg-regression-runner is readonly+is_background.
# Its subagentStop can arrive AFTER the stop gate allowed + bumped the task.
# The bump must preserve the open call record, the late stop must release
# the readonly flag, and the NEXT task's edits must produce NO violation
# (pre-fix: the bump wiped the record, the slug stuck in
# active_readonly_subagents, and every later task force-exhausted).
cat > "$readonly_fixture/.cursor/agents/bg-watcher.md" <<EOF
---
schema_version: 2
name: bg-watcher
description: background readonly reviewer (bg-regression-runner stand-in)
category: review
protocol: strict
readonly: true
is_background: true
model: inherit
tags: [review]
domains: [all]
---

Body content.
EOF
# --- runner path -----------------------------------------------------------
reset_state
printf '{}' | python3 "$SCRIPTS/hook_runner.py" sessionStart >/dev/null
# Write FIRST (before the bg reviewer opens), then satisfy the protocol.
printf '{"file_path":"src/api/auth.ts"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\nverify"}' \
  | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null
printf '{}' | python3 "$SCRIPTS/hook_runner.py" subagentStop >/dev/null
append_handoff "scenario 29 runner bg readonly"
# Launch the bg readonly reviewer; it stays OPEN across the gate.
(cd "$readonly_fixture" && \
  printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: bg-watcher\nwatch"}' \
    | python3 "$SCRIPTS/hook_runner.py" subagentStart >/dev/null)
assert "runner: gate allows + bumps with bg reviewer open" "allow" "$(runner_verdict '{}')"
open_after_bump="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
calls=[c for c in d.get("subagent_calls",[]) if not (c.get("stopped_at") or c.get("completed"))]
print(",".join(c.get("slug") or "" for c in calls))')"
assert "runner: open bg call record survives the bump" "bg-watcher" "$open_after_bump"
# Late stop arrives AFTER the bump: must close the record + free the slug.
printf '{}' | python3 "$SCRIPTS/hook_runner.py" subagentStop >/dev/null
actives_after="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
print(len(d.get("active_readonly_subagents") or []))')"
assert "runner: late stop releases the readonly flag" "0" "$actives_after"
# Next task's edit: a normal write, NO readonly violation.
printf '{"file_path":"src/next_task.py"}' | python3 "$SCRIPTS/hook_runner.py" afterFileEdit >/dev/null
viol_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("readonly_violations") or []))')"
wr_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("writes") or []))')"
assert "runner: next task's edit has NO readonly violation" "0" "$viol_n"
assert "runner: next task's edit recorded as a write" "1" "$wr_n"
# --- .sh path --------------------------------------------------------------
reset_state
pipe_json '{}' "$SCRIPTS/seed-session.sh" >/dev/null
pipe_json '{"file_path":"src/api/auth.ts"}' "$SCRIPTS/record-write.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose","prompt":"AGENT: qa-verifier\\nverify"}' \
  "$SCRIPTS/record-subagent-start.sh" >/dev/null
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
append_handoff "scenario 29 sh bg readonly"
(cd "$readonly_fixture" && \
  printf '%s' '{"subagent_type":"generalPurpose","prompt":"AGENT: bg-watcher\nwatch"}' \
    | bash "$SCRIPTS/record-subagent-start.sh" >/dev/null)
assert ".sh: gate allows + bumps with bg reviewer open" "allow" "$(gate_verdict '{}')"
pipe_json '{"subagent_type":"generalPurpose"}' "$SCRIPTS/record-subagent-stop.sh" >/dev/null
actives_after="$(python3 -c 'import json
d=json.load(open("'"$STATE_FILE"'"))
print(len(d.get("active_readonly_subagents") or []))')"
assert ".sh: late stop releases the readonly flag" "0" "$actives_after"
pipe_json '{"file_path":"src/next_task.py"}' "$SCRIPTS/record-write.sh" >/dev/null
viol_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("readonly_violations") or []))')"
wr_n="$(python3 -c 'import json;d=json.load(open("'"$STATE_FILE"'"));print(len(d.get("writes") or []))')"
assert ".sh: next task's edit has NO readonly violation" "0" "$viol_n"
assert ".sh: next task's edit recorded as a write" "1" "$wr_n"

printf "\n=== summary ===\n  passed: %s\n  failed: %s\n" "$pass" "$fail"
if (( fail > 0 )); then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
