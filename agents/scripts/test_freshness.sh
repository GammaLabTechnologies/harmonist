#!/usr/bin/env bash
# Tests for scan_agent_freshness.py.
#
#   1. Clean catalog -> no errors.
#   2. Hostile fixture with AngularJS + Python2 + Enzyme-context +
#      docker-compose-v1 + travis + GPT-3 -> multiple errors, exit 1.
#   3. --require-version warns on agents lacking `version`.
#   4. --stale-after warns on old `updated_at`.
#   5. --json shape parses.
#   6. Schema lint accepts `version` / `updated_at` / `deprecated`.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCAN="$PACK/agents/scripts/scan_agent_freshness.py"
LINT="$PACK/agents/scripts/lint_agents.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. Clean baseline
# ---------------------------------------------------------------------------

printf "\n=== 1: clean pack catalog ===\n"
python3 "$SCAN" >/dev/null 2>&1 && ok "baseline exits 0" || ko "baseline failed"

# ---------------------------------------------------------------------------
# 2. Hostile fixture
# ---------------------------------------------------------------------------

printf "\n=== 2: hostile deprecated-tech fixture ===\n"
dir="$TMP/hostile"
mkdir -p "$dir"
cat > "$dir/bad.md" <<'EOF'
---
schema_version: 2
name: Outdated Agent
description: references ancient stacks
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [backend]
domains: [all]
---

# Bad agent

AngularJS 1.x with bower and grunt in the build.
IE11 support needed, polyfills included.
Python 2.7 with nose tests.
text-davinci-003 for generation.
Travis CI runs docker-compose v1 builds.
EOF

set +e
out="$(python3 "$SCAN" --path "$dir" --json 2>&1)"
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "hostile exits 1" || ko "hostile exit=$rc"
printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
errs = d['counts']['error']
assert errs >= 4, f'expected >=4 errors, got {errs}'
rules = {f['rule'] for f in d['findings']}
assert 'js.angularjs-1x' in rules
assert 'py.python2' in rules
assert 'general.ie11' in rules
assert 'ai.gpt-3-or-davinci' in rules
" && ok "expected deprecated-tech rules fired" || ko "rules missing"

# ---------------------------------------------------------------------------
# 3. --require-version on a file without `version`
# ---------------------------------------------------------------------------

printf "\n=== 3: --require-version flags missing version ===\n"
dir2="$TMP/nover"
mkdir -p "$dir2"
cat > "$dir2/a.md" <<'EOF'
---
schema_version: 2
name: Agent Without Version
description: a modern agent but no version field
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [backend]
domains: [all]
---

# Modern content, no deprecated signals here at all.
EOF

out="$(python3 "$SCAN" --path "$dir2" --require-version --json 2>&1)"
if printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule']=='meta.no-version' for f in d['findings'])
"; then
  ok "meta.no-version fires"
else
  ko "no-version rule did not fire"
fi

# ---------------------------------------------------------------------------
# 4. --stale-after warns on very old updated_at
# ---------------------------------------------------------------------------

printf "\n=== 4: --stale-after warns on old updated_at ===\n"
dir3="$TMP/stale"
mkdir -p "$dir3"
cat > "$dir3/a.md" <<'EOF'
---
schema_version: 2
name: Old Agent
description: last touched in 2020
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [backend]
domains: [all]
version: "0.5.0"
updated_at: "2020-01-01"
---

# Was fine then, nobody read it since.
EOF

out="$(python3 "$SCAN" --path "$dir3" --stale-after 365 --json 2>&1)"
if printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert any(f['rule']=='meta.stale-updated-at' for f in d['findings'])
"; then
  ok "stale-updated-at fires"
else
  ko "stale rule did not fire"
fi

# ---------------------------------------------------------------------------
# 5. JSON shape
# ---------------------------------------------------------------------------

printf "\n=== 5: JSON shape ===\n"
out="$(python3 "$SCAN" --json 2>&1)"
if printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'findings' in d and 'agents' in d and 'counts' in d
assert isinstance(d['agents'], list)
"; then
  ok "JSON payload parses"
else
  ko "JSON malformed"
fi

# ---------------------------------------------------------------------------
# 6. Lint accepts the new optional fields
# ---------------------------------------------------------------------------

printf "\n=== 6: lint accepts version/updated_at/deprecated on strict agents ===\n"
# Strict reviewers now carry `version` + `updated_at` after W9. Full-
# pack lint has to pass to prove the new optional fields are accepted.
bash "$PACK/agents/scripts/lint-agents.sh" >/tmp/lint-full.out 2>&1 && \
  ok "full lint passes with new optional fields populated" || \
  { ko "full lint failed"; tail -5 /tmp/lint-full.out; }

# Tamper: change a strict agent's version to non-SemVer in a reversible
# way, confirm lint catches it, restore.
target="$PACK/agents/review/qa-verifier.md"
cp "$target" "$target.bak"
sed -i.tmp 's/^version: 1\.0\.0$/version: not-semver/' "$target" && rm -f "$target.tmp"
set +e
bash "$PACK/agents/scripts/lint-agents.sh" >/tmp/lint-bad.out 2>&1
rc=$?
set -e
mv "$target.bak" "$target"
if [[ "$rc" != "0" ]] && /usr/bin/grep -q "SemVer" /tmp/lint-bad.out; then
  ok "lint rejects non-SemVer version"
else
  ko "lint did NOT reject non-SemVer; rc=$rc"
fi

# ---------------------------------------------------------------------------
# 7. --vocab layers custom rules on top of the built-in set
# ---------------------------------------------------------------------------

printf "\n=== 7: --vocab appends custom rules ===\n"
vdir="$TMP/vocab"
mkdir -p "$vdir"
cat > "$vdir/org-rules.json" <<'EOF'
[
  {
    "id":          "org.internal-old-api",
    "severity":    "error",
    "pattern":     "legacy-internal-api",
    "message":     "references the internal legacy API we're sunsetting",
    "replacement": "our 2026 gateway at v2-gateway.internal"
  }
]
EOF

target="$TMP/custom-hit.md"
cat > "$target" <<'EOF'
---
schema_version: 2
name: Legacy Consumer
description: uses internal legacy api
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [backend]
domains: [all]
---

# Body

Connects to legacy-internal-api for order data.
EOF

set +e
python3 "$SCAN" --path "$target" --vocab "$vdir/org-rules.json" --json 2>&1 > /tmp/cv.out
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "custom rule produces error -> exit 1" || ko "rc=$rc"
/usr/bin/grep -q "org.internal-old-api" /tmp/cv.out && ok "custom rule id fires" || ko "rule didn't fire"

# ---------------------------------------------------------------------------
# 8. --vocab with extend_builtin=false REPLACES the built-in rules
# ---------------------------------------------------------------------------

printf "\n=== 8: --vocab extend_builtin=false replaces built-ins ===\n"
cat > "$vdir/replace.json" <<'EOF'
{
  "extend_builtin": false,
  "rules": [
    {
      "id":       "only.rule",
      "severity": "warn",
      "pattern":  "\\bneverrr\\b",
      "message":  "never match"
    }
  ]
}
EOF

# The `dir` with deliberately-deprecated content should produce ZERO
# findings now because the built-in rules are replaced with just
# "only.rule" which won't match.
set +e
out="$(python3 "$SCAN" --path "$dir/bad.md" --vocab "$vdir/replace.json" --json 2>&1)"
rc=$?
set -e
cnt_err="$(printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d['counts']['error'])
")"
[[ "$cnt_err" == "0" ]] && ok "built-in rules replaced (0 errors)" || ko "errors=$cnt_err"

# ---------------------------------------------------------------------------
# 9. --vocab id override replaces a built-in rule
# ---------------------------------------------------------------------------

printf "\n=== 9: same-id vocab rule overrides built-in ===\n"
cat > "$vdir/override.json" <<'EOF'
[
  {
    "id":       "py.python2",
    "severity": "info",
    "pattern":  "(?i)python 2\\.",
    "message":  "py2 mention acceptable in historical docs"
  }
]
EOF
set +e
out="$(python3 "$SCAN" --path "$dir/bad.md" --vocab "$vdir/override.json" --json 2>&1)"
rc=$?
set -e
# py.python2 was error by default; our override makes it info.
# Our hostile file had multiple errors; one less now.
py2_severity="$(printf '%s' "$out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for f in d['findings']:
    if f['rule']=='py.python2':
        print(f['severity']); break
else:
    print('none')
")"
[[ "$py2_severity" == "info" ]] && ok "py.python2 severity overridden to 'info'" || ko "severity=$py2_severity"

# ---------------------------------------------------------------------------
# 10. --print-vocab-schema emits the schema doc
# ---------------------------------------------------------------------------

printf "\n=== 10: --print-vocab-schema ===\n"
out="$(python3 "$SCAN" --print-vocab-schema 2>&1)"
printf '%s' "$out" | /usr/bin/grep -q "External rule files" && ok "schema doc printed" || ko "no schema"

# ---------------------------------------------------------------------------
# 11. Invalid --vocab JSON -> exit 2
# ---------------------------------------------------------------------------

printf "\n=== 11: invalid vocab JSON is rejected ===\n"
echo '[{"id": "bad", "pattern": "["}]' > "$vdir/broken.json"
set +e
python3 "$SCAN" --path "$target" --vocab "$vdir/broken.json" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "invalid regex -> exit 2" || ko "rc=$rc"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
