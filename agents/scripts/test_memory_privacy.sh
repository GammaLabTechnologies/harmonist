#!/usr/bin/env bash
# Tests for the memory-privacy defences:
#   * memory.py secret-pattern scanner
#   * upgrade.py's .gitignore hardening
#   * scan_memory_leaks.py git-history auditor

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. memory.py refuses to append an entry carrying a credential shape.
# ---------------------------------------------------------------------------

printf "\n=== 1: memory.py scans for secrets on append ===\n"
mem="$TMP/mem1"
mkdir -p "$mem"
cp "$PACK/memory/"*.py "$mem/"
cp "$PACK/memory/session-handoff.md" "$PACK/memory/decisions.md" "$PACK/memory/patterns.md" "$mem/"
export AGENT_PACK_HOOKS_STATE="$TMP/hs1.json"
echo '{"session_id":"8888","task_seq":0,"active_correlation_id":"8888-0"}' > "$AGENT_PACK_HOOKS_STATE"

set +e
err_out="$(python3 "$mem/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "Integrated Stripe" \
  --body "Wired up live billing with sk_live_$(printf '%.0s9' {1..24})" 2>&1 >/dev/null)"
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "refuses stripe-secret-shaped body" || ko "exit rc=$rc"
echo "$err_out" | grep -qi "Stripe secret" && ok "reports Stripe secret" || ko "reports Stripe secret"

# GitHub PAT
set +e
python3 "$mem/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "testing" \
  --body "token=ghp_$(printf '%.0sA' {1..36})" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "refuses GitHub PAT" || ko "GitHub PAT rc=$rc"

# Private key PEM
set +e
python3 "$mem/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "key rotation" \
  --body "rolled key: -----BEGIN RSA PRIVATE KEY-----
abc
-----END RSA PRIVATE KEY-----" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "refuses PEM block" || ko "PEM rc=$rc"

# Innocuous body must still pass.
set +e
python3 "$mem/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "boot" \
  --body "Integrated Stripe. Replace <STRIPE_KEY> with the live secret." >/dev/null
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "clean body accepted" || ko "clean body rc=$rc"

# --allow-secrets override works when user insists.
set +e
python3 "$mem/memory.py" append \
  --file session-handoff --kind state --status done \
  --summary "override" \
  --body "token ghp_$(printf '%.0sA' {1..36})" \
  --allow-secrets >/dev/null
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "--allow-secrets overrides the scanner" || ko "override rc=$rc"

unset AGENT_PACK_HOOKS_STATE

# ---------------------------------------------------------------------------
# 2. upgrade.py writes the memory-privacy block into .gitignore.
# ---------------------------------------------------------------------------

printf "\n=== 2: upgrade.py hardens .gitignore ===\n"
proj="$TMP/proj2"
mkdir -p "$proj"
cp "$PACK/AGENTS.md" "$proj/AGENTS.md"
# apply once
set +e
python3 "$PACK/agents/scripts/upgrade.py" --project "$proj" --pack "$PACK" --apply >/dev/null
rc=$?
set -e
[[ -f "$proj/.gitignore" ]] && ok ".gitignore created" || ko ".gitignore created"
grep -qF "harmonist: memory privacy" "$proj/.gitignore" && ok "privacy block inserted" || ko "privacy block inserted"
grep -qF ".cursor/memory/*.md" "$proj/.gitignore" && ok "memory md exclusion present" || ko "memory md exclusion"
grep -qF "!.cursor/memory/*.shared.md" "$proj/.gitignore" && ok "shared.md re-include present" || ko "shared.md re-include"

# idempotent: second apply adds nothing
before="$(cat "$proj/.gitignore")"
set +e
python3 "$PACK/agents/scripts/upgrade.py" --project "$proj" --pack "$PACK" --apply >/dev/null
set -e
after="$(cat "$proj/.gitignore")"
[[ "$before" == "$after" ]] && ok "second apply leaves .gitignore untouched" || ko "second apply mutated .gitignore"

# Pre-existing .gitignore survives (we append, not replace).
proj3="$TMP/proj3"
mkdir -p "$proj3"
cp "$PACK/AGENTS.md" "$proj3/AGENTS.md"
cat > "$proj3/.gitignore" <<'EOF'
node_modules/
.env
*.pyc
EOF
set +e
python3 "$PACK/agents/scripts/upgrade.py" --project "$proj3" --pack "$PACK" --apply >/dev/null
set -e
grep -qF "node_modules/" "$proj3/.gitignore" && ok "pre-existing entries preserved" || ko "pre-existing entries lost"
grep -qF "harmonist: memory privacy" "$proj3/.gitignore" && ok "privacy block appended" || ko "privacy block not appended"

# ---------------------------------------------------------------------------
# 3. scan_memory_leaks.py flags tracked + historical leaks.
# ---------------------------------------------------------------------------

printf "\n=== 3: scan_memory_leaks.py on a git repo ===\n"
repo="$TMP/repo"
mkdir -p "$repo"
cd "$repo"
git init -q
git config user.email "t@t"
git config user.name "t"
mkdir -p .cursor/memory
echo "accidentally-committed state" > .cursor/memory/session-handoff.md
git add .cursor/memory/session-handoff.md
git commit -q -m "oops"
# remove + commit to leave it in history only
git rm -q .cursor/memory/session-handoff.md
git commit -q -m "remove leaked memory"
# add a shared.md that must NOT be flagged
mkdir -p .cursor/memory
echo "team decision log" > .cursor/memory/decisions.shared.md
git add . && git commit -q -m "shared decisions"
cd - >/dev/null

set +e
out="$(python3 "$PACK/agents/scripts/scan_memory_leaks.py" --project "$repo" 2>&1)"
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "reports leak exit=1" || ko "exit rc=$rc"
echo "$out" | grep -qF "in history only" && ok "flags historical leak" || ko "flags historical leak"
echo "$out" | grep -qF "session-handoff.md" && ok "names the leaked file" || ko "names the leaked file"
echo "$out" | grep -qF "decisions.shared.md" && ko "shared.md must not be flagged" || ok "shared.md not flagged"
# JSON mode parses (script exits 1 when leaks exist, but JSON is still valid).
set +e
json_out="$(python3 "$PACK/agents/scripts/scan_memory_leaks.py" --project "$repo" --json 2>&1)"
set -e
if printf '%s' "$json_out" | python3 -c "import json,sys;d=json.load(sys.stdin);assert d['leaks']>=1" >/dev/null 2>&1; then
  ok "JSON output parses and reports leaks"
else
  ko "JSON output (first 200 chars: ${json_out:0:200})"
fi

# Clean repo: no leaks.
clean="$TMP/clean"
mkdir -p "$clean"
cd "$clean"
git init -q
git config user.email "t@t"
git config user.name "t"
echo "README" > README.md
git add README.md && git commit -q -m "init"
cd - >/dev/null
set +e
python3 "$PACK/agents/scripts/scan_memory_leaks.py" --project "$clean" >/dev/null
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "clean repo reports no leaks" || ko "clean repo rc=$rc"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
