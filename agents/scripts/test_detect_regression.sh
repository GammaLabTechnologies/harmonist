#!/usr/bin/env bash
# Tests for detect_regression_commands.py and its integration with upgrade.py.
#
# Fixture projects of several shapes:
#   1. Python + ruff + mypy + pytest
#   2. JS with pnpm lock + vitest + eslint + tsc
#   3. Rust (Cargo.toml)
#   4. Go (go.mod)
#   5. Empty project -> detector reports nothing; bg-regression keeps placeholder text

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/detect_regression_commands.py"
UPGRADE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/upgrade.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then ok "$label"; else ko "$label (missing: $needle)"; fi
}

# ---------------------------------------------------------------------------

printf "\n=== 1: python project (pytest + ruff + mypy) ===\n"
p1="$TMP/py"; mkdir -p "$p1"
cat > "$p1/pyproject.toml" <<'EOF'
[project]
name = "demo"

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF
out="$(python3 "$SCRIPT" --project "$p1" --json)"
assert_contains "pytest detected"  "pytest -xvs"   "$out"
assert_contains "ruff detected"    "ruff check ."  "$out"
assert_contains "mypy detected"    "mypy ."        "$out"

# ---------------------------------------------------------------------------

printf "\n=== 2: JS project with pnpm + vitest + eslint + tsc ===\n"
p2="$TMP/js"; mkdir -p "$p2"
cat > "$p2/package.json" <<'EOF'
{
  "name": "demo",
  "scripts": {
    "test": "vitest run",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "build": "vite build"
  },
  "devDependencies": {
    "vitest": "^1",
    "eslint": "^9",
    "typescript": "^5"
  }
}
EOF
touch "$p2/pnpm-lock.yaml"
out="$(python3 "$SCRIPT" --project "$p2" --json)"
assert_contains "pnpm test prefix"       "pnpm test"      "$out"
assert_contains "pnpm lint prefix"       "pnpm lint"      "$out"
assert_contains "pnpm typecheck prefix"  "pnpm typecheck" "$out"
assert_contains "pnpm build prefix"      "pnpm build"     "$out"

# Without lockfile -> npm run *
p2b="$TMP/jsnpm"; mkdir -p "$p2b"
cat > "$p2b/package.json" <<'EOF'
{"scripts":{"test":"jest"},"devDependencies":{"jest":"^29"}}
EOF
out="$(python3 "$SCRIPT" --project "$p2b" --json)"
assert_contains "npm run test"  "npm run test"  "$out"

# ---------------------------------------------------------------------------

printf "\n=== 3: Rust project ===\n"
p3="$TMP/rs"; mkdir -p "$p3"
cat > "$p3/Cargo.toml" <<'EOF'
[package]
name = "demo"
version = "0.1.0"
edition = "2021"
EOF
out="$(python3 "$SCRIPT" --project "$p3" --json)"
assert_contains "cargo test"    "cargo test"    "$out"
assert_contains "cargo clippy"  "cargo clippy"  "$out"

# ---------------------------------------------------------------------------

printf "\n=== 4: Go project ===\n"
p4="$TMP/go"; mkdir -p "$p4"
cat > "$p4/go.mod" <<'EOF'
module example.com/demo
go 1.22
EOF
out="$(python3 "$SCRIPT" --project "$p4" --json)"
assert_contains "go test"   "go test ./..."   "$out"
assert_contains "go vet"    "go vet ./..."    "$out"
assert_contains "go build"  "go build ./..."  "$out"

# ---------------------------------------------------------------------------

printf "\n=== 5: empty project -> renderer emits placeholder block ===\n"
p5="$TMP/empty"; mkdir -p "$p5"
rendered="$(python3 "$SCRIPT" --project "$p5" --render)"
assert_contains "renderer says 'No manifest'" "No manifest detected" "$rendered"

# ---------------------------------------------------------------------------

printf "\n=== 6: upgrade.py seeds bg-regression-runner only when placeholder text present ===\n"
proj="$TMP/fullproj"; mkdir -p "$proj"
cp "$PACK/AGENTS.md" "$proj/AGENTS.md"
cat > "$proj/pyproject.toml" <<'EOF'
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
EOF
python3 "$UPGRADE" --project "$proj" --pack "$PACK" --apply >/dev/null || true
bg="$proj/.cursor/agents/bg-regression-runner.md"
[[ -f "$bg" ]] && ok "bg-regression-runner.md created" || ko "bg-regression-runner.md missing"
grep -qF "pytest -xvs" "$bg" && ok "pytest command seeded" || ko "pytest command seeded"
grep -qF "ruff check ." "$bg" && ok "ruff command seeded" || ko "ruff command seeded"

# Re-run should NOT clobber now that the file has real commands.
printf "# MY CUSTOM EDIT\n" >> "$bg"
python3 "$UPGRADE" --project "$proj" --pack "$PACK" --apply >/dev/null || true
grep -qF "# MY CUSTOM EDIT" "$bg" && ok "customised bg stays untouched on re-upgrade" || ko "customised bg overwritten"

# ---------------------------------------------------------------------------

printf "\n=== 7: upgrade on an empty project seeds placeholder block, not silent ===\n"
proj2="$TMP/emptyproj"; mkdir -p "$proj2"
cp "$PACK/AGENTS.md" "$proj2/AGENTS.md"
python3 "$UPGRADE" --project "$proj2" --pack "$PACK" --apply >/dev/null || true
bg2="$proj2/.cursor/agents/bg-regression-runner.md"
[[ -f "$bg2" ]] && ok "bg file created even with no manifests" || ko "bg file missing for empty project"
grep -qF "No manifest detected" "$bg2" && ok "placeholder block present for manual fill-in" || ko "no placeholder block"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
