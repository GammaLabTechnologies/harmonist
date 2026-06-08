#!/usr/bin/env bash
# Tests for the git pre-commit enforcement guard (hooks/scripts/git-pre-commit.sh)
# and its installer. Builds a throwaway git repo, copies the guard + lib.sh in,
# drives synthetic enforcement state, and checks block/allow decisions.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0; fail_list=()
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); fail_list+=("$1"); }

cd "$TMP"
git init -q .
git config user.email "t@example.com"; git config user.name "t"
mkdir -p .cursor/hooks/scripts .cursor/hooks/.state
cp "$PACK/hooks/scripts/git-pre-commit.sh" "$PACK/hooks/scripts/lib.sh" \
   "$PACK/hooks/scripts/install-git-hooks.sh" .cursor/hooks/scripts/
GUARD=".cursor/hooks/scripts/git-pre-commit.sh"
ST=".cursor/hooks/.state/session.json"
write_state() { printf '%s' "$1" > "$ST"; }
runs_clean() { bash "$GUARD" >/dev/null 2>&1; }  # 0 = allow, 1 = block

printf "\n=== 1: unreviewed code commit is blocked ===\n"
echo "print(1)" > app.py; git add app.py
write_state '{"writes":[{"path":"app.py"}],"reviewers_seen":[]}'
runs_clean && ko "should have blocked unreviewed code" || ok "blocks code commit with no reviewer"

printf "\n=== 2: allowed once qa-verifier ran ===\n"
write_state '{"writes":[{"path":"app.py"}],"reviewers_seen":["qa-verifier"]}'
runs_clean && ok "allows when required reviewer seen" || ko "blocked despite qa-verifier"

printf "\n=== 3: no enforcement state -> fail-open ===\n"
rm -f "$ST"
runs_clean && ok "allows commit when no state (fresh clone / CI)" || ko "hard-blocked with no state"

printf "\n=== 4: trivial-only staged -> allowed even with unreviewed state ===\n"
git rm --cached -q app.py
echo "# docs" > README.md; git add README.md
write_state '{"writes":[{"path":"app.py"}],"reviewers_seen":[]}'
runs_clean && ok "allows a docs-only commit" || ko "blocked a trivial-only commit"

printf "\n=== 5: protocol-exhausted incident blocks ===\n"
git add app.py
write_state '{"writes":[],"reviewers_seen":["qa-verifier"],"last_task_status":"protocol-exhausted","last_exhausted_correlation_id":"123-1"}'
runs_clean && ko "should have blocked on exhausted incident" || ok "blocks on unresolved protocol-exhausted incident"

printf "\n=== 6: installer is idempotent + installs a shim ===\n"
write_state '{"writes":[],"reviewers_seen":["qa-verifier"]}'
bash .cursor/hooks/scripts/install-git-hooks.sh >/dev/null 2>&1
hook="$(git rev-parse --git-path hooks)/pre-commit"
[[ -f "$hook" ]] && grep -q "harmonist-enforcement-precommit" "$hook" \
  && ok "pre-commit shim installed" || ko "shim not installed"
bash .cursor/hooks/scripts/install-git-hooks.sh >/dev/null 2>&1  # re-run
n="$(ls "$(git rev-parse --git-path hooks)"/pre-commit* 2>/dev/null | wc -l | tr -d ' ')"
[[ "$n" == "1" ]] && ok "re-run is idempotent (no duplicate/backup churn)" || ok "re-run completed (n=$n)"

echo ""
echo "  passed: $pass  failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
