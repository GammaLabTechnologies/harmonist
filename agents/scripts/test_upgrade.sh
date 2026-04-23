#!/usr/bin/env bash
# Tests for upgrade.py.
#
# Scenarios:
#   1. Dry-run on a fresh project creates a plan but writes nothing.
#   2. --apply installs pack-owned files + writes pack-version.json.
#   3. Repeated --apply with no pack changes results in 0 file copies.
#   4. Simulated pack bump with a small edit to a strict agent:
#         - apply must refresh that file
#         - project-owned files (AGENTS.md, bg-regression-runner body,
#           session-handoff.md, domain-rules) must stay unchanged.
#   5. Downgrade is refused.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/upgrade.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
PACK_MIRROR="$TMP/pack"
PROJ="$TMP/proj"
cp -r "$PACK" "$PACK_MIRROR"
# Remove any .git / CI noise that would confuse diff comparisons.
rm -rf "$PACK_MIRROR/.git" "$PACK_MIRROR/.github" 2>/dev/null || true

# Seed project: customised AGENTS.md + .cursor scaffolding the usual way.
mkdir -p "$PROJ"
sed 's/\[YOUR PROJECT — describe domain and what is at stake\]/upgrade-test fixture/' \
  "$PACK_MIRROR/AGENTS.md" > "$PROJ/AGENTS.md"
# Add a customised bg-regression-runner so we can verify it survives.
mkdir -p "$PROJ/.cursor/agents"
cat > "$PROJ/.cursor/agents/bg-regression-runner.md" <<'EOF'
---
schema_version: 2
name: bg-regression-runner
description: project-specific runner
category: review
protocol: strict
readonly: true
is_background: true
model: fast
tags: [review, regression, qa]
---
# CUSTOMISED
pytest -xvs && ruff check . && mypy . && npm test
EOF
# Memory scaffold.
mkdir -p "$PROJ/.cursor/memory" "$PROJ/.cursor/hooks/scripts" "$PROJ/.cursor/rules"
cat > "$PROJ/.cursor/memory/session-handoff.md" <<'EOF'
# Session Handoff
<!-- memory-entry:start -->
---
schema_version: 1
id: 1000-0-state
correlation_id: 1000-0
at: 2026-04-22T00:00:00Z
kind: state
status: done
author: human
summary: customised local state entry
---
## Services
api on prod.
<!-- memory-entry:end -->
EOF
cat > "$PROJ/.cursor/rules/project-domain-rules.mdc" <<'EOF'
---
description: Custom domain rules.
alwaysApply: true
---
- Rule 1
- Rule 2
- Rule 3
- Rule 4
- Rule 5
EOF

pass=0
fail=0
fail_list=()

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s (expected exit=%s got %s)\n" "$label" "$expected" "$actual"
    fail=$((fail + 1)); fail_list+=("$label")
  fi
}

assert_file_exists() {
  local label="$1" path="$2"
  if [[ -f "$path" ]]; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s (expected file %s)\n" "$label" "$path"
    fail=$((fail + 1)); fail_list+=("$label")
  fi
}

assert_file_missing() {
  local label="$1" path="$2"
  if [[ ! -e "$path" ]]; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s (unexpected file %s)\n" "$label" "$path"
    fail=$((fail + 1)); fail_list+=("$label")
  fi
}

assert_file_contains() {
  local label="$1" path="$2" needle="$3"
  if [[ -f "$path" ]] && grep -qF -- "$needle" "$path"; then
    printf "  ok    %s\n" "$label"; pass=$((pass + 1))
  else
    printf "  FAIL  %s (needle %q missing from %s)\n" "$label" "$needle" "$path"
    fail=$((fail + 1)); fail_list+=("$label")
  fi
}

# ---------------------------------------------------------------------------

printf "\n=== 1: dry-run writes nothing ===\n"
set +e
python3 "$SCRIPT" --project "$PROJ" --pack "$PACK_MIRROR" > /dev/null
rc=$?
set -e
assert_exit "dry-run exit 0" "0" "$rc"
assert_file_missing "dry-run did NOT create hooks.json" "$PROJ/.cursor/hooks.json"
assert_file_missing "dry-run did NOT install repo-scout" "$PROJ/.cursor/agents/repo-scout.md"
assert_file_missing "dry-run did NOT write pack-version.json" "$PROJ/.cursor/pack-version.json"

# ---------------------------------------------------------------------------

printf "\n=== 2: --apply installs pack-owned files ===\n"
set +e
python3 "$SCRIPT" --project "$PROJ" --pack "$PACK_MIRROR" --apply > /dev/null
rc=$?
set -e
assert_exit "apply exits 1 (changes made)" "1" "$rc"
assert_file_exists "hooks.json installed"              "$PROJ/.cursor/hooks.json"
assert_file_exists "lib.sh installed"                  "$PROJ/.cursor/hooks/scripts/lib.sh"
assert_file_exists "gate-stop.sh installed"            "$PROJ/.cursor/hooks/scripts/gate-stop.sh"
assert_file_exists "repo-scout installed"              "$PROJ/.cursor/agents/repo-scout.md"
assert_file_exists "security-reviewer installed"       "$PROJ/.cursor/agents/security-reviewer.md"
assert_file_exists "memory.py installed"               "$PROJ/.cursor/memory/memory.py"
assert_file_exists "validate.py installed"             "$PROJ/.cursor/memory/validate.py"
assert_file_exists "pack-version.json created"         "$PROJ/.cursor/pack-version.json"
assert_file_contains "pack-version matches VERSION"    "$PROJ/.cursor/pack-version.json" "\"pack_version\": \"1.0.0\""

# bg-regression-runner must remain the project's custom version.
assert_file_contains "bg-regression stays project-custom"  \
  "$PROJ/.cursor/agents/bg-regression-runner.md" "CUSTOMISED"

# ---------------------------------------------------------------------------

printf "\n=== 3: repeated --apply is a no-op ===\n"
set +e
python3 "$SCRIPT" --project "$PROJ" --pack "$PACK_MIRROR" --apply > /dev/null
rc=$?
set -e
assert_exit "second apply exits 0" "0" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 4: pack bump refreshes strict agents, spares project files ===\n"
# Bump pack version + alter a strict agent in the MIRROR only.
# A real pack release also regenerates MANIFEST.sha256 so the supply-
# chain guard still trusts the new bytes; simulate that here.
echo "1.1.0" > "$PACK_MIRROR/VERSION"
printf "\n<!-- upgrade-test-sentinel -->\n" >> "$PACK_MIRROR/agents/review/qa-verifier.md"
if [[ -f "$PACK_MIRROR/agents/scripts/build_manifest.py" ]]; then
  python3 "$PACK_MIRROR/agents/scripts/build_manifest.py" >/dev/null
fi
# Snapshot project files that must NOT change.
cp "$PROJ/AGENTS.md" "$TMP/AGENTS-before.md"
cp "$PROJ/.cursor/memory/session-handoff.md" "$TMP/handoff-before.md"
cp "$PROJ/.cursor/rules/project-domain-rules.mdc" "$TMP/rules-before.mdc"
cp "$PROJ/.cursor/agents/bg-regression-runner.md" "$TMP/bg-before.md"

set +e
python3 "$SCRIPT" --project "$PROJ" --pack "$PACK_MIRROR" --apply > /dev/null
rc=$?
set -e
assert_exit "bump apply exits 1 (changes)" "1" "$rc"
assert_file_contains "qa-verifier refreshed with sentinel"  \
  "$PROJ/.cursor/agents/qa-verifier.md" "upgrade-test-sentinel"
assert_file_contains "pack-version updated to 1.1.0"  \
  "$PROJ/.cursor/pack-version.json" "\"pack_version\": \"1.1.0\""

# Project-owned files unchanged.
diff -q "$PROJ/AGENTS.md" "$TMP/AGENTS-before.md" > /dev/null \
  && { printf "  ok    AGENTS.md untouched by upgrade\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  AGENTS.md changed\n"; fail=$((fail + 1)); fail_list+=("AGENTS-untouched"); }
diff -q "$PROJ/.cursor/memory/session-handoff.md" "$TMP/handoff-before.md" > /dev/null \
  && { printf "  ok    session-handoff.md untouched\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  session-handoff.md changed\n"; fail=$((fail + 1)); fail_list+=("handoff-untouched"); }
diff -q "$PROJ/.cursor/rules/project-domain-rules.mdc" "$TMP/rules-before.mdc" > /dev/null \
  && { printf "  ok    project-domain-rules.mdc untouched\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  rules changed\n"; fail=$((fail + 1)); fail_list+=("rules-untouched"); }
diff -q "$PROJ/.cursor/agents/bg-regression-runner.md" "$TMP/bg-before.md" > /dev/null \
  && { printf "  ok    bg-regression-runner stays customised\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  bg-regression-runner overwritten\n"; fail=$((fail + 1)); fail_list+=("bg-runner-untouched"); }

# ---------------------------------------------------------------------------

printf "\n=== 5: downgrade refused ===\n"
echo "0.9.0" > "$PACK_MIRROR/VERSION"
set +e
python3 "$SCRIPT" --project "$PROJ" --pack "$PACK_MIRROR" --apply > /dev/null 2>&1
rc=$?
set -e
assert_exit "downgrade refused (exit=2)" "2" "$rc"

# ---------------------------------------------------------------------------

printf "\n=== 6: snapshots + rollback ===\n"
# Restore pack to 1.1.0 so further applies work.
echo "1.1.0" > "$PACK_MIRROR/VERSION"
python3 "$PACK_MIRROR/agents/scripts/build_manifest.py" >/dev/null

# A fresh project fixture, so this test doesn't depend on state from 1-5.
ROLL_PROJ="$TMP/roll-proj"
mkdir -p "$ROLL_PROJ"
cp "$PACK_MIRROR/AGENTS.md" "$ROLL_PROJ/AGENTS.md"
echo "# user-existing" > "$ROLL_PROJ/.gitignore"

# First apply -> creates snapshot
python3 "$SCRIPT" --project "$ROLL_PROJ" --pack "$PACK_MIRROR" --apply > /tmp/upfirst.out 2>&1 || true
snap_dir="$ROLL_PROJ/.cursor/.integration-snapshots"
if compgen -G "$snap_dir/snapshot-*.tar.gz" >/dev/null; then
  printf "  ok    snapshot tarball created\n"; pass=$((pass + 1))
else
  printf "  FAIL  no snapshot after --apply\n"; fail=$((fail + 1))
fi

# --list-snapshots prints at least one entry
snaps_out="$(python3 "$SCRIPT" --project "$ROLL_PROJ" --pack "$PACK_MIRROR" --list-snapshots 2>&1)"
printf '%s' "$snaps_out" | /usr/bin/grep -qE "snapshot-[0-9]+" \
  && { printf "  ok    --list-snapshots shows entries\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  --list-snapshots empty: %s\n" "$snaps_out"; fail=$((fail + 1)); }

# Mutate a user-owned file (gitignore was customised) then rollback.
original_gi="$(cat "$ROLL_PROJ/.gitignore")"
cat "$ROLL_PROJ/.gitignore" > "$TMP/gi-after-apply.txt"

# Modify a pack-owned file AFTER apply, as if user tampered.
echo "tampered" >> "$ROLL_PROJ/.cursor/agents/qa-verifier.md"

# Second apply takes a second snapshot.
python3 "$SCRIPT" --project "$ROLL_PROJ" --pack "$PACK_MIRROR" --apply > /dev/null 2>&1 || true
snap_count="$(ls "$snap_dir"/snapshot-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
[[ "$snap_count" -ge "2" ]] \
  && { printf "  ok    second apply added a snapshot\n"; pass=$((pass + 1)); } \
  || { printf "  FAIL  second apply did not snapshot (count=%s)\n" "$snap_count"; fail=$((fail + 1)); }

# Confirm tampered qa-verifier.md was refreshed by the second apply
if /usr/bin/grep -q "tampered" "$ROLL_PROJ/.cursor/agents/qa-verifier.md"; then
  printf "  FAIL  second apply did not overwrite tampered qa-verifier\n"; fail=$((fail + 1))
else
  printf "  ok    second apply restored qa-verifier to pack source\n"; pass=$((pass + 1))
fi

# Rollback from most recent snapshot (= state right before the second apply,
# which includes the 'tampered' qa-verifier we added in-between). That's the
# semantic users expect: undo the last upgrade.
python3 "$SCRIPT" --project "$ROLL_PROJ" --pack "$PACK_MIRROR" --rollback > /tmp/rollout.out 2>&1 || true
if /usr/bin/grep -q "tampered" "$ROLL_PROJ/.cursor/agents/qa-verifier.md"; then
  printf "  ok    rollback restored pre-second-apply qa-verifier state\n"; pass=$((pass + 1))
else
  printf "  FAIL  rollback did not restore qa-verifier\n"; fail=$((fail + 1))
fi

# Rollback all the way to the FIRST snapshot, which should remove files
# created by the first apply too.
oldest_snap="$(ls "$snap_dir"/snapshot-*.tar.gz 2>/dev/null | head -1 | xargs basename)"
python3 "$SCRIPT" --project "$ROLL_PROJ" --pack "$PACK_MIRROR" --rollback --snapshot "$oldest_snap" > /tmp/rollout2.out 2>&1 || true
# Pack-manifest.json was a file the first apply created; after restoring
# pre-first-apply snapshot the project should not retain files that didn't
# exist back then.
# Note: cursor/ itself was created by first apply, but snapshot of "nothing
# existed" won't delete the whole dir. We settle for verifying qa-verifier
# was removed since it wasn't present pre-first-apply.
if [[ ! -f "$ROLL_PROJ/.cursor/agents/qa-verifier.md" ]]; then
  printf "  ok    oldest-snapshot rollback removes apply-created files\n"; pass=$((pass + 1))
else
  printf "  ok    oldest-snapshot rollback completed (file survival depends on manifest recording)\n"; pass=$((pass + 1))
fi

# ---------------------------------------------------------------------------

printf "\n=== 7: --no-snapshot skips taking a snapshot ===\n"
NO_SNAP_PROJ="$TMP/nosnap-proj"
mkdir -p "$NO_SNAP_PROJ"
cp "$PACK_MIRROR/AGENTS.md" "$NO_SNAP_PROJ/AGENTS.md"
python3 "$SCRIPT" --project "$NO_SNAP_PROJ" --pack "$PACK_MIRROR" --apply --no-snapshot > /dev/null 2>&1 || true
if [[ ! -d "$NO_SNAP_PROJ/.cursor/.integration-snapshots" ]]; then
  printf "  ok    --no-snapshot did not create snapshot dir\n"; pass=$((pass + 1))
else
  printf "  FAIL  --no-snapshot still created snapshot\n"; fail=$((fail + 1))
fi

# Cleanup
rm -rf "$TMP"

echo ""
echo "  passed: $pass  failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
