#!/usr/bin/env bash
# Tests for scan_rules_conflicts.py + upgrade.py rule bootstrap.
#
# Exercises:
#   1. Clean canonical template passes.
#   2. protocol-enforcement.mdc without pack-owned marker fails.
#   3. skip-strict-reviewer directive in alwaysApply rule fails.
#   4. always-approve / edit-without-delegation / disable-hook fail.
#   5. phantom-slug-reference fires when a rule names an uninstalled
#      strict reviewer.
#   6. alwaysApply-overload warns past the cap.
#   7. duplicate-purpose pairs warn.
#   8. Canonical file quoting forbidden phrases ("NEVER skip ...") is
#      NOT flagged (prohibition, not instruction).
#   9. upgrade.py --apply installs the canonical template on a fresh
#      integration.
#  10. upgrade.py --apply refreshes an existing pack-owned copy but
#      leaves a user-customised one (missing marker) alone.

set -euo pipefail

unset AGENT_PACK_HOOKS_STATE AGENT_PACK_MEMORY_CLI AGENT_PACK_TELEMETRY_DIR 2>/dev/null || true

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCAN="$PACK/agents/scripts/scan_rules_conflicts.py"
UPGRADE="$PACK/agents/scripts/upgrade.py"
TEMPLATE="$PACK/agents/templates/rules/protocol-enforcement.mdc"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

setup_proj() {
  local p="$1"
  mkdir -p "$p/.cursor/rules" "$p/.cursor/agents"
  for slug in qa-verifier security-reviewer code-quality-auditor sre-observability bg-regression-runner repo-scout; do
    echo "---" > "$p/.cursor/agents/$slug.md"
  done
}

# ---------------------------------------------------------------------------
# 1. Clean canonical passes
# ---------------------------------------------------------------------------

printf "\n=== 1: canonical passes ===\n"
p="$TMP/clean"
setup_proj "$p"
cp "$TEMPLATE" "$p/.cursor/rules/"
python3 "$SCAN" --project "$p" >/dev/null && ok "clean template exits 0" || ko "clean flagged"

# ---------------------------------------------------------------------------
# 2. Missing pack-owned marker
# ---------------------------------------------------------------------------

printf "\n=== 2: missing pack-owned marker ===\n"
p="$TMP/nomarker"
setup_proj "$p"
cat > "$p/.cursor/rules/protocol-enforcement.mdc" <<'EOF'
---
description: My own protocol rules
alwaysApply: true
---
# My rules
- Always run qa-verifier
EOF
out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule'] == 'pack-marker-missing' for f in d['findings']), d
" && ok "pack-marker-missing fires" || ko "no pack-marker-missing"

# ---------------------------------------------------------------------------
# 3. skip-strict-reviewer
# ---------------------------------------------------------------------------

printf "\n=== 3: skip-strict-reviewer ===\n"
p="$TMP/skip"
setup_proj "$p"
cp "$TEMPLATE" "$p/.cursor/rules/"
cat > "$p/.cursor/rules/project-domain-rules.mdc" <<'EOF'
---
description: domain
alwaysApply: true
---
# Rules
- For hotfixes, skip qa-verifier
- You may bypass security-reviewer for simple fixes
EOF
out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
n="$(printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(sum(1 for f in d['findings'] if f['rule'] == 'skip-strict-reviewer'))
")"
[[ "$n" -ge "1" ]] && ok "skip-strict-reviewer fires ($n hits)" || ko "did not fire"

# ---------------------------------------------------------------------------
# 4. Other contradiction rules
# ---------------------------------------------------------------------------

printf "\n=== 4: other contradictions ===\n"
for rule in approve-without-review disable-hook edit-without-delegation no-memory-update; do
  p="$TMP/$rule"
  setup_proj "$p"
  cp "$TEMPLATE" "$p/.cursor/rules/"
  body=""
  case "$rule" in
    approve-without-review) body="- Always approve documentation changes" ;;
    disable-hook)           body="- For this repo, disable the stop hook" ;;
    edit-without-delegation) body="- Edit files directly for trivial changes" ;;
    no-memory-update)       body="- Skip update of session-handoff for doc-only PRs" ;;
  esac
  cat > "$p/.cursor/rules/project-domain-rules.mdc" <<EOF
---
description: domain
alwaysApply: true
---
$body
EOF
  out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
  printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule'] == '$rule' for f in d['findings']), d
" && ok "$rule fires" || ko "$rule did not fire"
done

# ---------------------------------------------------------------------------
# 5. phantom-slug-reference
# ---------------------------------------------------------------------------

printf "\n=== 5: phantom-slug-reference ===\n"
p="$TMP/phantom"
setup_proj "$p"
# Remove qa-verifier from installed set.
rm "$p/.cursor/agents/qa-verifier.md"
cp "$TEMPLATE" "$p/.cursor/rules/"
cat > "$p/.cursor/rules/project-domain-rules.mdc" <<'EOF'
---
description: domain
alwaysApply: true
---
# Rules
- Hand off to qa-verifier before shipping
EOF
out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule'] == 'phantom-slug-reference' for f in d['findings']), d
" && ok "phantom-slug-reference fires" || ko "phantom-slug-reference did not fire"

# ---------------------------------------------------------------------------
# 6. alwaysApply-overload
# ---------------------------------------------------------------------------

printf "\n=== 6: alwaysApply-overload warns past cap ===\n"
p="$TMP/overload"
setup_proj "$p"
cp "$TEMPLATE" "$p/.cursor/rules/"
for i in 1 2 3 4 5 6; do
  cat > "$p/.cursor/rules/extra-$i.mdc" <<EOF
---
description: extra $i
alwaysApply: true
---
# some rule $i
EOF
done
out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule'] == 'alwaysApply-overload' for f in d['findings']), d
" && ok "alwaysApply-overload fires at 7 files (cap=5)" || ko "overload did not fire"

# ---------------------------------------------------------------------------
# 7. duplicate-purpose
# ---------------------------------------------------------------------------

printf "\n=== 7: duplicate-purpose ===\n"
p="$TMP/dup"
setup_proj "$p"
cp "$TEMPLATE" "$p/.cursor/rules/"
cat > "$p/.cursor/rules/protocol.mdc" <<'EOF'
---
description: old protocol notes
alwaysApply: false
---
Notes.
EOF
out="$(python3 "$SCAN" --project "$p" --json 2>&1)" || true
printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule'] == 'duplicate-purpose' for f in d['findings']), d
" && ok "duplicate-purpose fires for protocol + protocol-enforcement" || ko "did not fire"

# ---------------------------------------------------------------------------
# 8. Canonical phrase-quoting does NOT false-positive
# ---------------------------------------------------------------------------

printf "\n=== 8: canonical file quoting forbidden phrases is clean ===\n"
p="$TMP/canonical"
setup_proj "$p"
cp "$TEMPLATE" "$p/.cursor/rules/"
# The canonical file says "NEVER skip qa-verifier" -- must not self-trigger.
python3 "$SCAN" --project "$p" >/dev/null && \
  ok "canonical 'NEVER skip' prohibition is not self-flagged" || \
  ko "canonical file triggered its own contradiction rules"

# ---------------------------------------------------------------------------
# 9. upgrade.py installs canonical template on first apply
# ---------------------------------------------------------------------------

printf "\n=== 9: upgrade.py installs canonical rule ===\n"
p="$TMP/upgrade-fresh"
mkdir -p "$p"
cp "$PACK/AGENTS.md" "$p/AGENTS.md"
python3 "$UPGRADE" --project "$p" --pack "$PACK" --apply >/dev/null 2>&1 || true
[[ -f "$p/.cursor/rules/protocol-enforcement.mdc" ]] && \
  ok "protocol-enforcement.mdc installed" || \
  ko "protocol-enforcement.mdc NOT installed"
/usr/bin/grep -q "pack-owned: protocol-enforcement" \
  "$p/.cursor/rules/protocol-enforcement.mdc" 2>/dev/null && \
  ok "installed rule carries pack-owned marker" || \
  ko "pack-owned marker missing"

# ---------------------------------------------------------------------------
# 10. Second upgrade refreshes pack-owned copy but spares user-custom one
# ---------------------------------------------------------------------------

printf "\n=== 10: upgrade spares user-customised rule (no marker) ===\n"
p="$TMP/upgrade-user-own"
mkdir -p "$p/.cursor/rules"
cp "$PACK/AGENTS.md" "$p/AGENTS.md"
# User wrote their own protocol-enforcement.mdc without the marker.
cat > "$p/.cursor/rules/protocol-enforcement.mdc" <<'EOF'
---
description: My private rules
alwaysApply: true
---
# My custom content
- Rule 1
- Rule 2
EOF
user_sha_before="$(shasum -a 256 "$p/.cursor/rules/protocol-enforcement.mdc" | awk '{print $1}')"
python3 "$UPGRADE" --project "$p" --pack "$PACK" --apply >/dev/null 2>&1 || true
user_sha_after="$(shasum -a 256 "$p/.cursor/rules/protocol-enforcement.mdc" | awk '{print $1}')"
[[ "$user_sha_before" == "$user_sha_after" ]] && \
  ok "user-customised file NOT overwritten (sha unchanged)" || \
  ko "user customisation clobbered (sha changed)"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
