#!/usr/bin/env bash
# Tests for check_pack_health.py. Run on the live pack and on several
# deliberately-broken mirror copies to confirm each failure mode is
# actually caught.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_pack_health.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

assert_exit() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && ok "$label" || ko "$label (expected $expected, got $actual)"
}

mirror() {
  # Copy pack to $1 minus .git noise.
  cp -r "$PACK" "$1"
  rm -rf "$1/.git" "$1/.github" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 1. Healthy pack -> exit 0.
# ---------------------------------------------------------------------------

printf "\n=== 1: healthy pack ===\n"
set +e
python3 "$SCRIPT" --pack "$PACK" >/dev/null 2>&1
rc=$?
set -e
assert_exit "healthy pack exits 0" "0" "$rc"

# ---------------------------------------------------------------------------
# 2. Stale index.json -> flagged.
# ---------------------------------------------------------------------------

printf "\n=== 2: stale index.json ===\n"
m1="$TMP/pack1"; mirror "$m1"
# Tamper with an agent's FRONTMATTER (indexable) so index is out of sync.
# Appending a body comment wouldn't change the derived index data.
python3 - "$m1/agents/review/security-reviewer.md" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1])
text = p.read_text()
text = re.sub(r'(^description: )([^\n]+)', r'\1[DRIFT] \2', text, count=1, flags=re.MULTILINE)
p.write_text(text)
PY
set +e
out="$(python3 "$SCRIPT" --pack "$m1" 2>&1)"
rc=$?
set -e
assert_exit "stale pack exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "index.json is stale" && ok "stale index flagged" || ko "stale index not flagged"

# ---------------------------------------------------------------------------
# 3. Truncated clone (<100 agents) -> flagged.
# ---------------------------------------------------------------------------

printf "\n=== 3: truncated clone ===\n"
m2="$TMP/pack2"; mirror "$m2"
# Delete most marketing agents to drop the total below MIN_AGENTS.
find "$m2/agents/marketing" -name "*.md" | tail -n +10 | xargs rm -f
find "$m2/agents/specialized" -name "*.md" | tail -n +5 | xargs rm -f
find "$m2/agents/engineering" -name "*.md" | tail -n +5 | xargs rm -f
find "$m2/agents/game-development" -name "*.md" -not -name 'README*' | xargs rm -f 2>/dev/null || true
# Rebuild index so the count reflects reality but below threshold.
python3 "$m2/agents/scripts/build_index.py" >/dev/null 2>&1 || true
set +e
out="$(python3 "$SCRIPT" --pack "$m2" 2>&1)"
rc=$?
set -e
assert_exit "truncated pack exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "truncated" && ok "truncated flagged" || \
printf '%s' "$out" | grep -qF "only" && ok "truncated flagged (count form)" || ko "truncated not flagged"

# ---------------------------------------------------------------------------
# 4. Missing required script -> flagged.
# ---------------------------------------------------------------------------

printf "\n=== 4: missing required script ===\n"
m3="$TMP/pack3"; mirror "$m3"
rm -f "$m3/agents/scripts/build_index.py"
set +e
out="$(python3 "$SCRIPT" --pack "$m3" 2>&1)"
rc=$?
set -e
assert_exit "missing script exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "build_index.py" && ok "missing build_index.py flagged" || ko "not flagged"

# ---------------------------------------------------------------------------
# 5. VERSION parse error.
# ---------------------------------------------------------------------------

printf "\n=== 5: bad VERSION file ===\n"
m4="$TMP/pack4"; mirror "$m4"
echo "rc-alpha" > "$m4/VERSION"
set +e
out="$(python3 "$SCRIPT" --pack "$m4" 2>&1)"
rc=$?
set -e
assert_exit "bad version exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "SemVer" && ok "SemVer error flagged" || ko "SemVer not flagged"

# ---------------------------------------------------------------------------
# 6. JSON output parses.
# ---------------------------------------------------------------------------

printf "\n=== 6: JSON output ===\n"
set +e
json_out="$(python3 "$SCRIPT" --pack "$PACK" --json 2>&1)"
set -e
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['summary']['failed'] == 0, d['summary']
assert d['summary']['total'] == 18, d['summary']
" >/dev/null 2>&1; then
  ok "JSON parses; 0 failures; 18 checks"
else
  ko "JSON parse or counts wrong"
fi

# ---------------------------------------------------------------------------
# 7. Not a pack -> exit 2.
# ---------------------------------------------------------------------------

printf "\n=== 7: non-pack directory ===\n"
m5="$TMP/emptydir"; mkdir -p "$m5"
set +e
python3 "$SCRIPT" --pack "$m5" >/dev/null 2>&1
rc=$?
set -e
assert_exit "non-pack exits 2" "2" "$rc"

# ---------------------------------------------------------------------------
# 8. --skip-slow still passes on healthy pack + is visibly faster.
# ---------------------------------------------------------------------------

printf "\n=== 8: --skip-slow mode ===\n"
set +e
out="$(python3 "$SCRIPT" --pack "$PACK" --skip-slow 2>&1)"
rc=$?
set -e
assert_exit "skip-slow exits 0 on healthy pack" "0" "$rc"
printf '%s' "$out" | grep -qF "(skipped)" && ok "lint/migrator reported as skipped" || ko "skipped marker missing"

# ---------------------------------------------------------------------------
# 9. Stale advertised counts -> count-claims flags the drift.
#    This is the guard that prevents the "pack ships 186 agents but
#    README still says 175" failure mode from ever returning.
# ---------------------------------------------------------------------------

printf "\n=== 9: stale advertised counts ===\n"
m6="$TMP/pack6"; mirror "$m6"
# Inject a bogus "999 agents" claim into README.md.
python3 - "$m6/README.md" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text()
# Add a line that looks like canonical marketing but with a wrong number.
text = text.replace(
    "# Harmonist\n",
    "# Harmonist\n\n> **999 agents** drift check.\n",
    1,
)
p.write_text(text)
PY
set +e
out="$(python3 "$SCRIPT" --pack "$m6" 2>&1)"
rc=$?
set -e
assert_exit "stale-claim pack exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "count-claims" && ok "count-claims check fired" || ko "count-claims not flagged"
printf '%s' "$out" | grep -qF "999" && ok "bad count (999) reported" || ko "bad count not shown in failure"

# And a bogus per-category count in the AGENTS.md table.
m7="$TMP/pack7"; mirror "$m7"
python3 - "$m7/AGENTS.md" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1])
text = p.read_text()
# Flip the engineering row count to something obviously wrong.
text = re.sub(
    r"(\|\s*`engineering`\s*\|[^|]*\|[^|]*\|\s*)\d+(\s*\|)",
    r"\g<1>777\g<2>",
    text,
    count=1,
)
p.write_text(text)
PY
set +e
out="$(python3 "$SCRIPT" --pack "$m7" 2>&1)"
rc=$?
set -e
assert_exit "stale-category pack exits 1" "1" "$rc"
printf '%s' "$out" | grep -qF "engineering" && ok "wrong category count flagged" || ko "category drift not flagged"

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
