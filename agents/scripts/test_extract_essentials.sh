#!/usr/bin/env bash
# Tests for scripts/extract_essentials.py.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/extract_essentials.py"
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

# --- Test 1: explicit marker wins ------------------------------------------
cat > "$TMP/with-marker.md" <<'EOF'
---
name: test-a
category: engineering
protocol: persona
tags: [backend]
---

## Identity
You are the tester.

## Core Mission
Do tests.

## Deep Reference

## Implementation details
This should NOT appear in the essentials output.

```python
print("deep stuff")
```

## More deep material
Also not essentials.
EOF

out="$(python3 "$SCRIPT" "$TMP/with-marker.md")"
assert_contains "marker: keeps identity" "You are the tester." "$out"
assert_contains "marker: keeps core mission" "Do tests." "$out"
assert_not_contains "marker: omits deep section" "This should NOT appear" "$out"
assert_not_contains "marker: omits code in deep section" 'print("deep stuff")' "$out"

# --- Test 2: budget cut when no marker -------------------------------------
python3 - "$TMP/no-marker.md" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
body = ["---", "name: test-b", "category: engineering", "protocol: persona", "tags: [backend]", "---", ""]
# First section (essentials)
body += ["## Identity", "Short identity line."]
# Pad to >80 non-blank lines
for i in range(90):
    body.append(f"Line number {i} with some content.")
# New section after budget -- should be cut
body += ["", "## Later Section", "This should be cut."]
p.write_text("\n".join(body))
PY

out="$(python3 "$SCRIPT" "$TMP/no-marker.md")"
assert_contains "budget: keeps early content" "Short identity line." "$out"
assert_not_contains "budget: cuts later section" "This should be cut." "$out"

# --- Test 3: full body kept when short ------------------------------------
cat > "$TMP/short.md" <<'EOF'
---
name: test-c
category: review
protocol: strict
tags: [review]
---

## Identity
Small agent.

## Rules
- one
- two
EOF

out="$(python3 "$SCRIPT" "$TMP/short.md")"
assert_contains "short: keeps rules" "- two" "$out"

# --- Test 4: --out-dir writes *.essentials.md ------------------------------
mkdir -p "$TMP/out"
python3 "$SCRIPT" --out-dir "$TMP/out" "$TMP/with-marker.md" >/dev/null
if [[ -f "$TMP/out/with-marker.essentials.md" ]]; then
  printf "  ok    --out-dir: writes <slug>.essentials.md\n"; pass=$((pass + 1))
else
  printf "  FAIL  --out-dir: expected file not written\n"; fail=$((fail + 1))
fi

# --- Test 5: --stats produces one line per input --------------------------
stats="$(python3 "$SCRIPT" --stats "$TMP/with-marker.md" "$TMP/no-marker.md" "$TMP/short.md")"
lines="$(printf '%s' "$stats" | grep -cE '[-][>]' || true)"
if [[ "$lines" -ge "3" ]]; then
  printf "  ok    --stats: one line per input\n"; pass=$((pass + 1))
else
  printf "  FAIL  --stats: expected >=3 entries, got %s\n" "$lines"; fail=$((fail + 1))
fi

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
