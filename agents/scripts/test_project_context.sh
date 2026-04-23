#!/usr/bin/env bash
# Tests for scripts/project_context.py.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/project_context.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s\n    needle: %s\n" "$label" "$needle"; fail=$((fail + 1))
  fi
}
assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    printf "  FAIL  %s  (unexpected: %s)\n" "$label" "$needle"; fail=$((fail + 1))
  else
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  fi
}

# --- Test 1: extracts the three default sections ----------------------------
cat > "$TMP/AGENTS.md" <<'EOF'
# Orchestrator

You are the lead engineer.

## Platform Stack

| Layer | Tech |
|-------|------|
| Backend | Go 1.22 |
| DB | PostgreSQL 16 |

## Modules

- `billing/`  — payments, invoices

## Invariants

1. No floats for money.
2. Migrations are append-only.

## Resilience

- exponential backoff with jitter
EOF

out="$(python3 "$SCRIPT" --path "$TMP/AGENTS.md")"
assert_contains "header present"      "PROJECT PRECEDENCE"   "$out"
assert_contains "stack present"       "Go 1.22"              "$out"
assert_contains "modules present"     "billing/"             "$out"
assert_contains "invariants present"  "No floats for money"  "$out"
assert_not_contains "resilience skipped by default" "exponential backoff" "$out"

# --- Test 2: missing AGENTS.md exits 1 --------------------------------------
set +e
python3 "$SCRIPT" --path "$TMP/does-not-exist.md" >/dev/null 2>&1
rc=$?
set -e
if [[ "$rc" == "1" ]]; then
  printf "  ok    missing AGENTS.md exits 1\n"; pass=$((pass + 1))
else
  printf "  FAIL  missing AGENTS.md exit=%s\n" "$rc"; fail=$((fail + 1))
fi

# --- Test 3: max-chars bound is respected -----------------------------------
short="$(python3 "$SCRIPT" --path "$TMP/AGENTS.md" --max-chars 200)"
len=${#short}
if [[ "$len" -le 210 ]]; then
  printf "  ok    max-chars bound (len=%s)\n" "$len"; pass=$((pass + 1))
else
  printf "  FAIL  max-chars exceeded (len=%s)\n" "$len"; fail=$((fail + 1))
fi

# --- Test 4: monorepo --focus picks nearest AGENTS.md, root adds layers ------
ROOT="$TMP/mono"
mkdir -p "$ROOT/packages/payments/src"
cat > "$ROOT/AGENTS.md" <<'EOF'
# Root

## Platform Stack
- monorepo root: Turborepo

## Modules
- packages/payments
- packages/web

## Invariants
- every package must pass the shared regression suite
EOF

cat > "$ROOT/packages/payments/AGENTS.md" <<'EOF'
# Payments package

## Platform Stack
- Node 22 with Fastify
- Postgres 16

## Invariants
- monetary values are BigInt minor units
- idempotency keys mandatory on every mutation
EOF

focus_out="$(python3 "$SCRIPT" --focus "$ROOT/packages/payments/src" 2>&1)"
assert_contains "focus finds nearest package AGENTS.md"  "Fastify"            "$focus_out"
assert_contains "focus includes package invariant"       "idempotency keys"   "$focus_out"
assert_contains "focus layers root sources footer"       "Preamble sources"   "$focus_out"
assert_contains "focus footer mentions root"             "/mono/AGENTS.md"     "$focus_out"
# Package AGENTS.md takes precedence over root for the same section.
assert_not_contains "root Turborepo is overridden by package stack" "Turborepo" "$focus_out"

# No --focus: walks up from CWD as before.
(cd "$ROOT" && python3 "$SCRIPT" > "$TMP/root-out.txt" 2>&1) || true
root_out="$(cat "$TMP/root-out.txt")"
assert_contains "no-focus picks root AGENTS.md"      "Turborepo"         "$root_out"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
