#!/usr/bin/env bash
# Tests for the hardening checklist + its stdlib validator
# (playbooks/checklists/). Confirms the shipped checklist is valid and that
# the validator rejects a malformed one.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
V="$PACK/playbooks/checklists/validate.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0; fail_list=()
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); fail_list+=("$1"); }

[[ -f "$V" ]] || { echo "fatal: validator missing at $V"; exit 1; }

printf "\n=== 1: shipped checklist is valid ===\n"
set +e
out="$(python3 "$V" 2>&1)"; rc=$?
set -e
[[ "$rc" -eq 0 ]] && ok "validator exits 0 on the shipped checklist" || ko "validator rc=$rc: $out"
printf '%s' "$out" | grep -q "section(s)" && ok "validator reports section/item counts" || ko "no summary line"

printf "\n=== 2: malformed checklist is rejected ===\n"
bad="$TMP/bad.json"
echo '[{"title":"x","slug":"X bad","intro":"y","checklist":[{"point":"p","priority":"nope"}]}]' > "$bad"
set +e
python3 "$V" --file "$bad" --quiet >/dev/null 2>&1; rcbad=$?
set -e
[[ "$rcbad" -eq 1 ]] && ok "bad slug + bad priority + missing details rejected (exit 1)" || ko "invalid checklist passed (rc=$rcbad)"

printf "\n=== 3: missing file -> exit 2 ===\n"
set +e
python3 "$V" --file "$TMP/nope.json" >/dev/null 2>&1; rcmiss=$?
set -e
[[ "$rcmiss" -eq 2 ]] && ok "missing file exits 2" || ko "missing file rc=$rcmiss"

echo ""
echo "  passed: $pass  failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
