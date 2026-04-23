#!/usr/bin/env bash
# Tests for verify_integration.py.
#
# Builds two synthetic project fixtures (one minimal/bad, one fully
# integrated/good) and checks that the script flags exactly the right
# set of failures.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/verify_integration.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
fail_list=()

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s  (expected %s got %s)\n" "$label" "$expected" "$actual"; fail=$((fail + 1))
    fail_list+=("$label")
  fi
}

assert_has_failure() {
  local label="$1" check_name="$2" output="$3"
  if printf '%s' "$output" | grep -qE "\b${check_name}\b.*✖|\b${check_name}\b.*✗|\[error.*${check_name}"; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s  (check '%s' did not fail)\n" "$label" "$check_name"; fail=$((fail + 1))
    fail_list+=("$label")
  fi
}

assert_has_pass() {
  local label="$1" check_name="$2" output="$3"
  if printf '%s' "$output" | grep -qE "✓ .*${check_name}"; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s  (check '%s' did not pass)\n" "$label" "$check_name"; fail=$((fail + 1))
    fail_list+=("$label")
  fi
}

# ---------------------------------------------------------------------------
# Fixture 1: bare project with only AGENTS.md template -> should fail nearly everything
# ---------------------------------------------------------------------------

BAD="$TMP/bad-project"
mkdir -p "$BAD"
cp "$PACK/AGENTS.md" "$BAD/AGENTS.md"

set +e
out="$(python3 "$SCRIPT" --project "$BAD" 2>&1)"
rc=$?
set -e

printf "\n=== Fixture 1: minimal project (template AGENTS.md copied verbatim) ===\n"
assert_exit "exit code 1 on failures" "1" "$rc"
assert_has_failure "agents-md-customized flagged" "agents-md-customized" "$out"
assert_has_failure "agents-md-invariants flagged" "agents-md-invariants" "$out"
assert_has_failure "agents-md-customize-comments flagged" "agents-md-customize-comments" "$out"
assert_has_failure "strict-agents missing" "strict-agents-installed" "$out"
assert_has_failure "memory-setup missing" "memory-setup" "$out"
assert_has_failure "hooks-json missing" "hooks-json" "$out"
assert_has_failure "cursor-rules missing" "cursor-rules" "$out"

# ---------------------------------------------------------------------------
# Fixture 2: fully integrated project. Pass.
# ---------------------------------------------------------------------------

GOOD="$TMP/good-project"
mkdir -p "$GOOD"
# Start from the pack's own AGENTS.md but customise it so template markers disappear.
sed \
  -e 's/\[YOUR PROJECT — describe domain and what is at stake\]/a fintech backend handling customer payments/' \
  -e 's/\[language, framework, ORM, database, cache\]/Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, Redis 7/' \
  -e 's/\[framework, language, bundler, styling, state\]/React 18, TypeScript, Vite, Tailwind, Zustand/' \
  -e 's/\[list third-party services\]/Stripe, Twilio/' \
  -e 's/\[containers, CI\/CD, deploy method\]/Docker, GitHub Actions, Kubernetes/' \
  -e 's/\[test frameworks, tools\]/pytest, vitest/' \
  -e 's/\[tool, current version\]/Alembic v1.13/' \
  -e 's/\[list running services\]/api, worker, scheduler/' \
  -e 's/\[path\]/\/srv\/fintech/' \
  -e 's/\[dev\/staging\/prod\]/staging, prod/' \
  -e 's|`module-a/`|`backend/`|' \
  -e 's|`module-b/`|`frontend/`|' \
  "$PACK/AGENTS.md" > "$GOOD/AGENTS.md"

# Strip CUSTOMIZE comments (verify_integration.py asserts none remain in
# project-owned prose) and rewrite the Invariants section so it is not
# the template verbatim. The GOOD fixture represents a project where
# the integrator actually did the work.
python3 - "$GOOD/AGENTS.md" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
# Drop every <!-- CUSTOMIZE: ... --> comment.
text = re.sub(r"<!--\s*CUSTOMIZE\b[^>]*-->\n?", "", text, flags=re.IGNORECASE)
# Replace the template Invariants list with project-specific ones.
replacement = """## Invariants

1. All monetary amounts use Decimal(scale=2); floats are banned in the payment path.
2. Every Stripe webhook validates the HMAC signature before dispatching.
3. Idempotency keys are required on every PaymentIntent creation call.
4. PII in application logs is a release blocker; redact before emit.
5. Alembic migrations are append-only; never edit a merged revision.
"""
# Match from "## Invariants" up to the next top-level section or the
# pack-owned marker, whichever comes first.
text = re.sub(
    r"## Invariants\n.*?(?=\n<!--\s*pack-owned:begin|\n## [A-Z])",
    replacement,
    text,
    count=1,
    flags=re.DOTALL,
)
p.write_text(text)
PY

# Ensure index.json reference
grep -q "agents/index.json" "$GOOD/AGENTS.md" || echo "See harmonist/agents/index.json for routing." >> "$GOOD/AGENTS.md"

# .cursor dirs
mkdir -p "$GOOD/.cursor/agents" "$GOOD/.cursor/memory" "$GOOD/.cursor/hooks/scripts" "$GOOD/.cursor/rules"
# strict agents
for f in "$PACK/agents/orchestration/repo-scout.md" \
         "$PACK/agents/review/"*.md; do
  cp "$f" "$GOOD/.cursor/agents/"
done
# specialist agents (3)
cp "$PACK/agents/engineering/engineering-backend-architect.md" "$GOOD/.cursor/agents/"
cp "$PACK/agents/engineering/engineering-security-engineer.md" "$GOOD/.cursor/agents/"
cp "$PACK/agents/engineering/engineering-devops-automator.md" "$GOOD/.cursor/agents/"
# Customise bg-regression-runner
cat > "$GOOD/.cursor/agents/bg-regression-runner.md" <<'EOF'
---
schema_version: 2
name: bg-regression-runner
description: Runs tests, linting, type checks, and builds in the background.
category: review
protocol: strict
readonly: true
is_background: true
model: fast
tags: [review, regression, qa]
distinguishes_from: [qa-verifier, testing-reality-checker, testing-performance-benchmarker]
disambiguation: Background execution of project test/lint/build commands.
---

You are the background regression runner for this project. Run exactly these:

  pytest -xvs
  ruff check .
  mypy .
  vitest run
  npm run build

Return concise failure-oriented output. Do not attempt fixes.
EOF
# memory setup
cp "$PACK/memory/memory.py" "$PACK/memory/validate.py" "$GOOD/.cursor/memory/"
cp "$PACK/memory/session-handoff.md" "$PACK/memory/decisions.md" "$PACK/memory/patterns.md" "$GOOD/.cursor/memory/"
# add a real state entry via the CLI
export AGENT_PACK_HOOKS_STATE="$TMP/hooks-state.json"
cat > "$AGENT_PACK_HOOKS_STATE" <<EOF
{"session_id": "9999", "task_seq": 0, "active_correlation_id": "9999-0"}
EOF
python3 "$GOOD/.cursor/memory/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "Integration bootstrap: FastAPI + Postgres, staging deployed" \
  --body "## Services
api, worker, scheduler on staging

## Recent Changes
- Integrated harmonist

## Open Issues
- none" >/dev/null
unset AGENT_PACK_HOOKS_STATE
# Delete the template 0-0 entry so only the real one remains
python3 - "$GOOD/.cursor/memory/session-handoff.md" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = re.sub(
    r"<!-- memory-entry:start -->\s*---\s*[^<]*?id: 0-0-state[^<]*?<!-- memory-entry:end -->\s*",
    "", text, count=1, flags=re.DOTALL,
)
p.write_text(text)
PY
# hooks.json
cp "$PACK/hooks/hooks.json" "$GOOD/.cursor/hooks.json"
# hook scripts
cp "$PACK/hooks/scripts/"*.sh "$GOOD/.cursor/hooks/scripts/"
chmod +x "$GOOD/.cursor/hooks/scripts/"*.sh
# cursor rules: pack-owned protocol + project-owned domain
cp "$PACK/agents/templates/rules/protocol-enforcement.mdc" \
   "$GOOD/.cursor/rules/protocol-enforcement.mdc"
cat > "$GOOD/.cursor/rules/project-domain-rules.mdc" <<'EOF'
---
description: Project-specific fintech rules. Violations cause real bugs.
alwaysApply: true
---

- Use BigDecimal for all monetary amounts; never float.
- Every payment must have an idempotency key.
- Stripe webhooks validate HMAC signatures before dispatching.
- PII in logs is a release blocker.
- Migrations are append-only; no modifying historical revisions.
EOF
# .gitignore
echo ".cursor/memory/*.md" > "$GOOD/.gitignore"

# Run verifier
set +e
out_good="$(python3 "$SCRIPT" --project "$GOOD" 2>&1)"
rc_good=$?
set -e

printf "\n=== Fixture 2: fully integrated project ===\n"
assert_exit "exit code 0 on clean project" "0" "$rc_good"
assert_has_pass "agents-md-customized passes"     "agents-md-customized"     "$out_good"
assert_has_pass "agents-md-invariants passes"     "agents-md-invariants"     "$out_good"
assert_has_pass "agents-md-customize-comments passes" "agents-md-customize-comments" "$out_good"
assert_has_pass "strict-agents-installed passes"  "strict-agents-installed"  "$out_good"
assert_has_pass "hooks-json passes"               "hooks-json"               "$out_good"
assert_has_pass "hook-scripts passes"             "hook-scripts"             "$out_good"
assert_has_pass "memory-setup passes"             "memory-setup"             "$out_good"
assert_has_pass "memory-not-template passes"      "memory-not-template"      "$out_good"
assert_has_pass "cursor-rules passes"             "cursor-rules"             "$out_good"
assert_has_pass "bg-regression-customized passes" "bg-regression-customized" "$out_good"

# ---------------------------------------------------------------------------
# Fixture 3: JSON output mode
# ---------------------------------------------------------------------------

set +e
json_out="$(python3 "$SCRIPT" --project "$GOOD" --json 2>&1)"
set -e
if printf '%s' "$json_out" | python3 -c "import json,sys;d=json.load(sys.stdin);assert d['summary']['errors']==0;print('ok')" >/dev/null 2>&1; then
  printf "\n  ok    JSON output parses and reports 0 errors\n"; pass=$((pass + 1))
else
  printf "\n  FAIL  JSON output invalid or reports errors\n"; fail=$((fail + 1))
  fail_list+=("json-output")
fi

echo ""
echo "  passed: $pass  failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
