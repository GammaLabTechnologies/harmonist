#!/usr/bin/env bash
# Tests for the Python version guard.
#
# Exercises:
#   1. refresh_py_guard.py injects the canonical block idempotently.
#   2. --check flags tampering and exits 1.
#   3. Every targeted script still compiles.
#   4. The guard aborts with exit 3 and a readable message when run on
#      Python 3.8 (simulated via a fake interpreter shim).
#   5. The bash guard in _bash_py_guard.sh refuses old python via $PYTHON.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REFRESH="$PACK/agents/scripts/refresh_py_guard.py"
GUARD="$PACK/agents/scripts/_bash_py_guard.sh"

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. Idempotency: second run is a no-op.
# ---------------------------------------------------------------------------

printf "\n=== 1: refresh is idempotent ===\n"
out="$(python3 "$REFRESH" 2>&1)"
out2="$(python3 "$REFRESH" 2>&1)"
if printf '%s' "$out2" | /usr/bin/grep -qE 'UPDATE|STALE'; then
  ko "second refresh reported changes -- not idempotent"
else
  ok "second refresh is a clean no-op"
fi

# ---------------------------------------------------------------------------
# 2. --check exits 1 on tampered guard.
# ---------------------------------------------------------------------------

printf "\n=== 2: --check flags a tampered guard ===\n"
python3 "$REFRESH" --check >/dev/null && ok "baseline --check passes" || ko "baseline --check failed"

victim="$PACK/agents/scripts/build_index.py"
cp "$victim" "$victim.bak"
# Replace the min version 3.9 with 3.99 so the guard no longer matches snippet.
python3 - <<PY
import pathlib, re
p = pathlib.Path("$victim")
t = p.read_text()
t2 = t.replace("(3, 9)", "(9, 99)", 1)
assert t != t2, "tamper did not change source"
p.write_text(t2)
PY
if python3 "$REFRESH" --check >/dev/null 2>&1; then
  ko "--check failed to detect tampering"
else
  ok "--check detected tampering"
fi
mv "$victim.bak" "$victim"
python3 "$REFRESH" --check >/dev/null && ok "--check passes again after restore" || ko "--check still failing after restore"

# ---------------------------------------------------------------------------
# 3. Every targeted script compiles.
# ---------------------------------------------------------------------------

printf "\n=== 3: all scripts compile on the current interpreter ===\n"
compiled=0; borked=0
while IFS= read -r rel; do
  [[ -z "$rel" ]] && continue
  if python3 -m py_compile "$PACK/$rel" >/dev/null 2>&1; then
    compiled=$((compiled + 1))
  else
    borked=$((borked + 1))
    echo "    compile FAILED: $rel"
  fi
done < <(python3 - <<PY
import sys; sys.path.insert(0, "$PACK/agents/scripts")
from refresh_py_guard import TARGETS
for t in TARGETS: print(t)
PY
)
[[ "$borked" -eq 0 ]] && ok "all $compiled target scripts compile" || ko "$borked scripts failed to compile"

# ---------------------------------------------------------------------------
# 4. Simulate Python 3.8 by patching sys.version_info and exec-ing only
#    the guard block.
# ---------------------------------------------------------------------------

printf "\n=== 4: guard aborts on simulated Python 3.8 ===\n"
simulation_out="$(python3 - 2>&1 <<'PY'
import re, sys
src = open("agents/scripts/build_index.py").read()
m = re.search(r"# === PY-GUARD:BEGIN ===(.*?)# === PY-GUARD:END ===", src, re.DOTALL)
body = m.group(1)
ns = {}
class V:
    def __init__(self, t): self.t = t
    def __lt__(self, other): return self.t < other
    def __getitem__(self, i): return self.t[i]
# Patch sys so the guard sees 3.8.
_orig_info = sys.version_info
sys.version_info = V((3, 8, 0))
sys.argv = ["build_index.py"]
try:
    exec(body, {"_asp_sys": sys})
except SystemExit as e:
    print("EXIT", e.code)
finally:
    sys.version_info = _orig_info
PY
)"
printf '%s' "$simulation_out" | /usr/bin/grep -q "requires Python 3.9+" && \
  ok "guard message mentions minimum version" || ko "no min-version message"
printf '%s' "$simulation_out" | /usr/bin/grep -q "brew install python@3.12" && \
  ok "guard includes macOS install hint" || ko "no macOS hint"
printf '%s' "$simulation_out" | /usr/bin/grep -q "EXIT 3" && \
  ok "guard exits with code 3" || ko "wrong exit code"

# ---------------------------------------------------------------------------
# 5. bash guard refuses old python via $PYTHON shim.
# ---------------------------------------------------------------------------

printf "\n=== 5: _bash_py_guard.sh refuses old python ===\n"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT
fake="$TMP/python3"
cat >"$fake" <<'EOF'
#!/usr/bin/env bash
# Fake interpreter: claims to be Python 3.8 to the guard.
case "$*" in
  *"import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"*)
    exit 1 ;;
  *"print(\"%d.%d\""*)
    echo "3.8"
    exit 0 ;;
  *)
    exit 0 ;;
esac
EOF
chmod +x "$fake"

out="$(PYTHON="$fake" bash -c "source '$GUARD'; require_python_39" 2>&1)" || rc=$?
if /usr/bin/grep -q "requires 3.9+" <<<"$out"; then
  ok "bash guard emits refusal message"
else
  ko "bash guard output: $out"
fi
[[ "${rc:-0}" -eq 3 ]] && ok "bash guard returns exit 3" || ko "bash guard exit code = ${rc:-?}"

# Bash guard with a missing binary.
out="$(PYTHON="$TMP/does-not-exist" bash -c "source '$GUARD'; require_python_39" 2>&1)" || rc2=$?
/usr/bin/grep -q "no .* on PATH" <<<"$out" && ok "bash guard handles missing interpreter" || ko "bash guard missing-binary msg: $out"

# Bash guard happy path with real python3.
out="$(unset PYTHON; bash -c "source '$GUARD'; require_python_39 && echo OK" 2>&1)"
/usr/bin/grep -q "^OK$" <<<"$out" && ok "bash guard passes on real python3" || ko "bash guard happy path broken: $out"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
