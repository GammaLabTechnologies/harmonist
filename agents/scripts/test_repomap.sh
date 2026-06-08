#!/usr/bin/env bash
# Tests for repomap.py -- the zero-dependency code map.
#
# Builds small fixture projects (Python import chain + a JS relative-import
# chain) and asserts build / search / explore / deps / dependents / impact /
# affected / refresh / status behave correctly. Pure stdlib, no Cursor.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/repomap.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

PROJ="$TMP/proj"
mkdir -p "$PROJ/src" "$PROJ/tests" "$PROJ/web/a" "$PROJ/web/b"

# --- Python import chain: fee <- billing <- checkout ; test_billing -> billing
cat > "$PROJ/src/fee.py" <<'EOF'
def calc_fee(amount):
    return amount * 0.03
class FeePolicy:
    pass
EOF
cat > "$PROJ/src/billing.py" <<'EOF'
from src.fee import calc_fee
def charge(x):
    return calc_fee(x)
EOF
cat > "$PROJ/src/checkout.py" <<'EOF'
from src.billing import charge
def run():
    return charge(10)
EOF
cat > "$PROJ/tests/test_billing.py" <<'EOF'
from src.billing import charge
def test_charge():
    assert charge(100) == 3.0
EOF

# --- JS relative-import chain: util <- service
cat > "$PROJ/web/a/util.js" <<'EOF'
export function formatMoney(n) { return '$' + n; }
EOF
cat > "$PROJ/web/b/service.js" <<'EOF'
import { formatMoney } from '../a/util.js';
export const price = (n) => formatMoney(n);
EOF

pass=0; fail=0; fail_list=()
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); fail_list+=("$1"); }
has() { printf '%s' "$1" | grep -qF -- "$2"; }

# ---------------------------------------------------------------------------
printf "\n=== 1: build ===\n"
build_json="$(python3 "$SCRIPT" build --project "$PROJ" --json)"
files="$(printf '%s' "$build_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["files"])')"
edges="$(printf '%s' "$build_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["edges"])')"
[[ "$files" == "6" ]] && ok "indexed 6 files" || ko "files=$files (expected 6)"
[[ "$edges" -ge "4" ]] && ok "resolved >=4 import edges" || ko "edges=$edges (expected >=4)"

# ---------------------------------------------------------------------------
printf "\n=== 2: search / explore ===\n"
s="$(python3 "$SCRIPT" search calc_fee --project "$PROJ")"
has "$s" "src/fee.py" && ok "search finds calc_fee in fee.py" || ko "search miss: $s"
e="$(python3 "$SCRIPT" explore "fee charge" --project "$PROJ")"
{ has "$e" "calc_fee" && has "$e" "charge"; } && ok "explore groups fee+charge symbols" || ko "explore miss: $e"

# ---------------------------------------------------------------------------
printf "\n=== 3: deps / dependents ===\n"
dep="$(python3 "$SCRIPT" dependents src/fee.py --project "$PROJ")"
has "$dep" "src/billing.py" && ok "dependents(fee) includes billing" || ko "dependents miss: $dep"
dn="$(python3 "$SCRIPT" deps src/billing.py --project "$PROJ")"
has "$dn" "src/fee.py" && ok "deps(billing) includes fee" || ko "deps miss: $dn"

# ---------------------------------------------------------------------------
printf "\n=== 4: impact / affected (transitive) ===\n"
imp="$(python3 "$SCRIPT" impact src/fee.py --project "$PROJ")"
{ has "$imp" "src/billing.py" && has "$imp" "src/checkout.py" && has "$imp" "tests/test_billing.py"; } \
  && ok "impact(fee) = billing + checkout + test" || ko "impact miss: $imp"
aff="$(python3 "$SCRIPT" affected src/fee.py --project "$PROJ")"
has "$aff" "tests/test_billing.py" && ! has "$aff" "src/checkout.py" \
  && ok "affected(fee) = only the test file" || ko "affected wrong: $aff"

# ---------------------------------------------------------------------------
printf "\n=== 5: JS relative imports resolve ===\n"
jdep="$(python3 "$SCRIPT" dependents web/a/util.js --project "$PROJ")"
has "$jdep" "web/b/service.js" && ok "JS relative import edge resolved" || ko "JS edge miss: $jdep"

# ---------------------------------------------------------------------------
printf "\n=== 6: refresh is incremental + idempotent ===\n"
r1="$(python3 "$SCRIPT" refresh --project "$PROJ" --json)"
idx="$(printf '%s' "$r1" | python3 -c 'import json,sys;print(json.load(sys.stdin)["indexed"])')"
[[ "$idx" == "0" ]] && ok "clean refresh re-indexes nothing" || ko "refresh indexed=$idx (expected 0)"
# Touch one file -> status pending, refresh re-indexes exactly it.
printf '\ndef extra():\n    return 1\n' >> "$PROJ/src/fee.py"
st="$(python3 "$SCRIPT" status --project "$PROJ" --json)"
pend="$(printf '%s' "$st" | python3 -c 'import json,sys;print(json.load(sys.stdin)["pending"])')"
[[ "$pend" == "1" ]] && ok "status reports 1 pending after edit" || ko "pending=$pend (expected 1)"
r2="$(python3 "$SCRIPT" refresh --project "$PROJ" --json)"
idx2="$(printf '%s' "$r2" | python3 -c 'import json,sys;print(json.load(sys.stdin)["indexed"])')"
[[ "$idx2" == "1" ]] && ok "refresh re-indexes only the changed file" || ko "refresh indexed=$idx2 (expected 1)"

# ---------------------------------------------------------------------------
printf "\n=== 7: status on a never-built project ===\n"
EMPTY="$TMP/empty"; mkdir -p "$EMPTY"
set +e
python3 "$SCRIPT" status --project "$EMPTY" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "status exits 1 when not built" || ko "status rc=$rc (expected 1)"

echo ""
echo "  passed: $pass  failed: $fail"
if [[ "$fail" -gt 0 ]]; then
  for f in "${fail_list[@]}"; do printf "    - %s\n" "$f"; done
  exit 1
fi
exit 0
