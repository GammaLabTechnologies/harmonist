#!/usr/bin/env bash
# Integration tests for the memory CLI + validator.
#
# Every test runs in an isolated tmp memory directory: the CLI writes
# there, the validator reads from there. No Cursor or hook state needed.

set -euo pipefail

PACK_MEMORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cp "$PACK_MEMORY/memory.py" "$PACK_MEMORY/validate.py" "$TMP/"
cp "$PACK_MEMORY/session-handoff.md" "$PACK_MEMORY/decisions.md" "$PACK_MEMORY/patterns.md" "$TMP/"

# Fake the hooks' state file so memory.py uses a deterministic correlation_id.
HOOKS_STATE="$TMP/hooks-state.json"
cat > "$HOOKS_STATE" <<'JSON'
{
  "session_id": "9999999999",
  "task_seq": 0,
  "active_correlation_id": "9999999999-0"
}
JSON
export AGENT_PACK_HOOKS_STATE="$HOOKS_STATE"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

pass=0
fail=0
fail_list=()

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

reset() {
  cp "$PACK_MEMORY/session-handoff.md" "$TMP/session-handoff.md"
  cp "$PACK_MEMORY/decisions.md" "$TMP/decisions.md"
  cp "$PACK_MEMORY/patterns.md" "$TMP/patterns.md"
}

# ---------------------------------------------------------------------------

printf "\n=== 1: validator accepts the shipped templates ===\n"
if python3 "$TMP/validate.py" --path "$TMP" >/dev/null 2>&1; then rc=0; else rc=1; fi
assert "templates pass validate.py" "0" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 2: CLI appends valid state entry + auto-fills correlation_id ===\n"
reset
cd "$TMP"
id="$(python3 "$TMP/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "Added Stripe webhook handler" \
  --tags payments,backend \
  --body "## Changes
- backend/payments/webhook.ts added
- migration V42 applied

## Open issues
- none")"
assert "append returned expected id" "9999999999-0-state" "$id"
if python3 "$TMP/validate.py" --path "$TMP" >/dev/null 2>&1; then rc=0; else rc=1; fi
assert "file still valid after append" "0" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 3: appending wrong kind is rejected ===\n"
reset
cd "$TMP"
if python3 "$TMP/memory.py" append \
  --file session-handoff --kind decision --status done \
  --summary "wrong kind" \
  --body "this should be rejected because session-handoff requires kind=state" \
  >/dev/null 2>&1; then
  rc=0
else
  rc=1
fi
assert "kind mismatch rejected" "1" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 4: empty body rejected ===\n"
reset
cd "$TMP"
if python3 "$TMP/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "empty" \
  --body " " >/dev/null 2>&1; then
  rc=0
else
  rc=1
fi
assert "empty body rejected" "1" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 5: duplicate id rejected and file rolled back ===\n"
reset
cd "$TMP"
# First append (succeeds)
python3 "$TMP/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "first" --body "twenty characters or more aaaa bbbb" >/dev/null
size_before=$(stat -f %z "$TMP/session-handoff.md" 2>/dev/null || stat -c %s "$TMP/session-handoff.md")
# Manipulate state to force same id on second append
cat > "$HOOKS_STATE" <<'JSON'
{
  "session_id": "9999999999",
  "task_seq": 0,
  "active_correlation_id": "9999999999-0"
}
JSON
# Second append should either get a distinct id (-2 suffix) OR be rejected.
id2="$(python3 "$TMP/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "second" --body "another body with more than twenty chars aaaa")"
assert "second append gets deduped id (-2 suffix)" "9999999999-0-state-2" "$id2"
if python3 "$TMP/validate.py" --path "$TMP" >/dev/null 2>&1; then rc=0; else rc=1; fi
assert "file still valid after deduplicated append" "0" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 6: hand-edited invalid entry caught by validator ===\n"
reset
cat >> "$TMP/session-handoff.md" <<'EOF'

<!-- memory-entry:start -->
---
id: broken
correlation_id: not-a-number
at: never
kind: state
status: unknown
author: ghost
summary: completely broken
---

Some body text.

<!-- memory-entry:end -->
EOF
if python3 "$TMP/validate.py" --path "$TMP" >/dev/null 2>&1; then rc=0; else rc=1; fi
assert "broken hand-edit caught" "1" "$rc"

# ---------------------------------------------------------------------------

printf '\n=== 7: non-monotonic "at" detected ===\n'
reset
cat >> "$TMP/patterns.md" <<'EOF'

<!-- memory-entry:start -->
---
id: 9999999999-0-pattern
correlation_id: 9999999999-0
at: 2030-01-01T00:00:00Z
kind: pattern
status: done
author: orchestrator
summary: future pattern
---

Long enough body for the schema validator to accept here.

<!-- memory-entry:end -->

<!-- memory-entry:start -->
---
id: 9999999999-1-pattern
correlation_id: 9999999999-1
at: 2020-01-01T00:00:00Z
kind: pattern
status: done
author: orchestrator
summary: past pattern
---

Long enough body for the schema validator to accept here.

<!-- memory-entry:end -->
EOF
if python3 "$TMP/validate.py" --path "$TMP" >/dev/null 2>&1; then rc=0; else rc=1; fi
assert "backwards-in-time entries rejected" "1" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 8: list / latest / current-id ===\n"
reset
cd "$TMP"
python3 "$TMP/memory.py" append --file session-handoff --kind state --status done \
  --summary "first task" --body "twenty characters or more xyz 1" >/dev/null
python3 "$TMP/memory.py" append --file session-handoff --kind state --status done \
  --summary "same correlation id second entry" --body "twenty characters or more xyz 2" >/dev/null
count=$(python3 "$TMP/memory.py" list --file session-handoff | wc -l | tr -d ' ')
# Template ships with 1 placeholder state entry; plus the two we just appended = 3.
assert "list returns template + two appended entries" "3" "$count"
latest_count=$(python3 "$TMP/memory.py" latest --file session-handoff --kind state --n 1 | grep -c 'memory-entry:start')
assert "latest --n 1 returns one entry" "1" "$latest_count"
cid=$(python3 "$TMP/memory.py" current-id)
assert "current-id reads from hooks state" "9999999999-0" "$cid"

# ---------------------------------------------------------------------------

printf "\n=== scenario: search across files ===\n"
# Seed a few entries with different kinds / tags / summaries.
python3 "$TMP/memory.py" append --file session-handoff --kind state \
    --status in_progress --summary "bootstrap initial state" \
    --tags bootstrap,core --body "Body mentions Laravel and Livewire stack." >/dev/null
python3 "$TMP/memory.py" append --file decisions --kind decision \
    --status done --summary "adopt GraphQL for public API" \
    --tags architecture,graphql --body "Decision: move from REST to GraphQL." >/dev/null
python3 "$TMP/memory.py" append --file patterns --kind pattern \
    --status done --summary "retry with exponential backoff" \
    --tags reliability --body "When a downstream flakes, retry with jitter." >/dev/null

# Text query hits a summary.
out="$(python3 "$TMP/memory.py" search --query "graphql" 2>&1)"
if printf "%s" "$out" | /usr/bin/grep -q "adopt GraphQL"; then
  printf "  ok    search finds a summary hit\n"; pass=$((pass + 1))
else
  printf "  FAIL  search did not find GraphQL summary\n"; fail=$((fail + 1))
fi

# Tag filter narrows results.
out="$(python3 "$TMP/memory.py" search --tag reliability 2>&1)"
if printf "%s" "$out" | /usr/bin/grep -q "exponential backoff" && \
   ! printf "%s" "$out" | /usr/bin/grep -q "graphql"; then
  printf "  ok    search --tag filters correctly\n"; pass=$((pass + 1))
else
  printf "  FAIL  tag filter broken\n"; fail=$((fail + 1))
fi

# --kind filter.
out="$(python3 "$TMP/memory.py" search --kind decision 2>&1)"
if printf "%s" "$out" | /usr/bin/grep -q "decision" && \
   ! printf "%s" "$out" | /usr/bin/grep -q "state"; then
  printf "  ok    search --kind filters correctly\n"; pass=$((pass + 1))
else
  printf "  FAIL  kind filter broken\n"; fail=$((fail + 1))
fi

# --json shape.
json_out="$(python3 "$TMP/memory.py" search --query "bootstrap" --json 2>&1)"
if printf "%s" "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'hits' in d and 'count' in d
assert d['count'] >= 1
"; then
  printf "  ok    search --json payload parses\n"; pass=$((pass + 1))
else
  printf "  FAIL  search --json malformed\n"; fail=$((fail + 1))
fi

# No-match returns non-zero.
set +e
python3 "$TMP/memory.py" search --query "xyzzy-no-such-string-ever" >/dev/null 2>&1
rc=$?
set -e
if [[ "$rc" == "1" ]]; then
  printf "  ok    search exits 1 on zero matches\n"; pass=$((pass + 1))
else
  printf "  FAIL  search exit code on no-match: %s\n" "$rc"; fail=$((fail + 1))
fi

# ---------------------------------------------------------------------------

printf "\n=== scenario: rotate archives older entries ===\n"
# Seed 5 entries in decisions.md beyond the one already added.
for i in 1 2 3 4 5; do
  python3 "$TMP/memory.py" bump-task >/dev/null
  python3 "$TMP/memory.py" append --file decisions --kind decision \
    --status done --summary "decision #$i" \
    --body "Context body long enough to satisfy the validator's 20-char minimum for entry #$i." \
    >/dev/null
done

# dry-run should not mutate.
before_size="$(wc -l < "$TMP/decisions.md")"
out="$(python3 "$TMP/memory.py" rotate --file decisions --keep-last 2 --dry-run 2>&1)"
after_size="$(wc -l < "$TMP/decisions.md")"
if [[ "$before_size" == "$after_size" ]] && printf "%s" "$out" | /usr/bin/grep -q "would rotate"; then
  printf "  ok    rotate --dry-run leaves file untouched\n"; pass=$((pass + 1))
else
  printf "  FAIL  dry-run mutated / no message\n"; fail=$((fail + 1))
fi

# Real rotate.
out="$(python3 "$TMP/memory.py" rotate --file decisions --keep-last 2 2>&1)"
if printf "%s" "$out" | /usr/bin/grep -qE "rotated [0-9]+ entry"; then
  printf "  ok    rotate moved older entries\n"; pass=$((pass + 1))
else
  printf "  FAIL  rotate did not run\n"; fail=$((fail + 1))
fi

# Archive file created and live file kept at least the requested count.
archive_count="$(ls "$TMP"/decisions-archive-*.md 2>/dev/null | wc -l | tr -d ' ')"
[[ "$archive_count" -ge "1" ]] && { printf "  ok    archive file created\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  no archive file\n"; fail=$((fail + 1)); }

# Validate the archive.
python3 "$TMP/validate.py" --path "$TMP" --strict >/tmp/va.out 2>&1 && \
  { printf "  ok    archive + live pass validation\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  validation after rotate: "; cat /tmp/va.out; fail=$((fail + 1)); }

# Rotate refuses to empty the live file.
set +e
python3 "$TMP/memory.py" rotate --file decisions --keep-last 0 >/dev/null 2>&1
rc=$?
set -e
if [[ "$rc" == "2" ]]; then
  printf "  ok    rotate refuses when keep-last <= 0\n"; pass=$((pass + 1))
else
  printf "  FAIL  rotate accepted empty-keep (rc=%s)\n" "$rc"; fail=$((fail + 1))
fi

# ---------------------------------------------------------------------------

printf "\n=== scenario: dedupe warn on identical summary ===\n"
# Seed one entry, then try to append again with same summary.
python3 "$TMP/memory.py" append --file patterns --kind pattern \
    --status done --summary "prefer pytest fixtures over global setUp" \
    --body "Pytest fixtures scope precisely; unittest setUp scopes the whole class." \
    >/dev/null
python3 "$TMP/memory.py" bump-task >/dev/null

set +e
python3 "$TMP/memory.py" append --file patterns --kind pattern \
    --status done --summary "prefer pytest fixtures over global setUp" \
    --body "Different body but same summary triggers the dedupe guard." \
    >/tmp/dup.out 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && { printf "  ok    dup-summary append refused (exit 2)\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  dup-summary exit=%s\n" "$rc"; fail=$((fail + 1)); }
/usr/bin/grep -q "already has the same summary" /tmp/dup.out && \
  { printf "  ok    dedupe error message mentions the conflict\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  no dedupe message\n"; fail=$((fail + 1)); }

# --allow-duplicate overrides.
set +e
python3 "$TMP/memory.py" append --file patterns --kind pattern \
    --status done --summary "prefer pytest fixtures over global setUp" \
    --body "Intentional re-emission of the same insight -- e.g. after a refactor reconfirmed it." \
    --allow-duplicate >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "0" ]] && { printf "  ok    --allow-duplicate overrides the guard\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  --allow-duplicate exit=%s\n" "$rc"; fail=$((fail + 1)); }

# Different summaries are unaffected.
python3 "$TMP/memory.py" bump-task >/dev/null
set +e
python3 "$TMP/memory.py" append --file patterns --kind pattern \
    --status done --summary "treat retries as observability, not reliability" \
    --body "A retry that hides an error is just a slower error with worse debuggability." \
    >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "0" ]] && { printf "  ok    distinct summary appends normally\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  distinct summary exit=%s\n" "$rc"; fail=$((fail + 1)); }

printf "\n=== scenario: migrations.py skeleton ===\n"
cp "$PACK_MEMORY/migrations.py" "$TMP/migrations.py"
# With zero migrations registered and every shipped entry at v1, the
# script should exit 0 and print the reassuring no-op message.
reset
set +e
python3 "$TMP/migrations.py" --path "$TMP" >/tmp/migrations.out 2>&1
rc=$?
set -e
[[ "$rc" == "0" ]] && { printf "  ok    migrations.py exits 0 on clean v1 corpus\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  migrations.py exit=%s (out=%s)\n" "$rc" "$(cat /tmp/migrations.out)"; fail=$((fail + 1)); }
grep -q "no migrations registered" /tmp/migrations.out \
  && { printf "  ok    migrations.py prints the skeleton message\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  skeleton message missing\n"; fail=$((fail + 1)); }

# Inject an entry with an unknown schema_version and confirm the
# script flags it with a non-zero exit.
cat >> "$TMP/patterns.md" <<'EOF'

<!-- memory-entry:start -->
---
schema_version: 99
id: stale-entry-test
correlation_id: 1-0
at: 2026-04-23T12:00:00Z
kind: pattern
status: done
author: orchestrator
summary: synthetic entry with impossible schema_version
---

Body text long enough to pass the minimum check for validator happiness.

<!-- memory-entry:end -->
EOF
set +e
python3 "$TMP/migrations.py" --path "$TMP" >/tmp/migrations.out 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && { printf "  ok    migrations.py exits 1 on unknown schema_version\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  migrations.py did not flag v99 (rc=%s)\n" "$rc"; fail=$((fail + 1)); }

printf "\n=== summary ===\n  passed: %s\n  failed: %s\n" "$pass" "$fail"
if (( fail > 0 )); then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
