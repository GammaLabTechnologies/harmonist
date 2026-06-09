#!/usr/bin/env bash
# Tests for merge_agents_md.py.
#
# Scenarios:
#   1. Fresh copy of pack AGENTS.md: dry-run reports 0 replaced / 0 inserted.
#   2. Project AGENTS.md has customised project-owned sections + stale
#      pack-owned section -- merge refreshes the pack-owned block, leaves
#      project-owned verbatim.
#   3. Pack introduces a new pack-owned block -- merge inserts it at the
#      end with a "please review" header.
#   4. Project file has NO markers (pre-marker integration) -- merger
#      refuses with a bootstrap-migration hint.

set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/merge_agents_md.py"
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Pack AGENTS template (canonical: AGENTS.template.md; legacy fallback).
PACK_TPL="$PACK/AGENTS.template.md"; [[ -f "$PACK_TPL" ]] || PACK_TPL="$PACK/AGENTS.md"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0

ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------

printf "\n=== 1: merge is idempotent (apply once, second run is a no-op) ===\n"
# A verbatim template copy is no longer a no-op: the merge rewrites the
# literal `harmonist/` path prefix to the actual pack dir name. Apply once,
# then confirm the second (dry) run reports nothing left to change.
# Use a CONTROLLED pack dir name (not $PACK): the real checkout may be
# named anything -- including literally `harmonist`, where substitution is
# a correct no-op (scenario 5) and the assertion below would be wrong.
proj1="$TMP/proj1"
mkdir -p "$proj1/vendored-pack"
cp "$PACK_TPL" "$proj1/vendored-pack/AGENTS.template.md"
cp "$PACK_TPL" "$proj1/AGENTS.md"
python3 "$SCRIPT" --pack "$proj1/vendored-pack" --project "$proj1" --apply >/dev/null || true
out="$(python3 "$SCRIPT" --pack "$proj1/vendored-pack" --project "$proj1" || true)"
echo "$out" | grep -qF "already up to date" && ok "reports up-to-date after apply" || ko "reports up-to-date after apply"
# Substitution actually happened: pack-owned content no longer hard-codes
# `harmonist/` (the controlled pack dir is named "vendored-pack").
if ! grep -qF "harmonist/agents/index.json" "$proj1/AGENTS.md" \
   && grep -qF "vendored-pack/agents/index.json" "$proj1/AGENTS.md"; then
  ok "pack dir prefix substituted in pack-owned blocks"
else
  ko "pack dir prefix substituted in pack-owned blocks"
fi

# ---------------------------------------------------------------------------

printf "\n=== 2: customised project, stale pack block -> refresh only the pack block ===\n"
proj2="$TMP/proj2"
mkdir -p "$proj2"
cp "$PACK_TPL" "$proj2/AGENTS.md"
# Customise a project-owned section (Invariants) + corrupt a pack-owned one (Precedence).
python3 - "$proj2/AGENTS.md" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
# Replace the Invariants block with a project-specific one.
text = re.sub(
    r"## Invariants.*?(?=---\n## Topology|\n## Topology|\n<!-- pack-owned:begin id=\"topology\")",
    ("## Invariants\n\n1. All money in BigDecimal.\n2. TON wallet nanotons only.\n3. Idempotent deposits.\n4. PROJECT-SPECIFIC-MARKER-42\n\n---\n\n"),
    text, count=1, flags=re.DOTALL,
)
# Mutate the precedence block body (stale pack content).
text = text.replace(
    "When multiple sources of advice collide",
    "STALE-PRECEDENCE-TEXT (pack will overwrite this)",
    1,
)
p.write_text(text, encoding="utf-8")
PY

out="$(python3 "$SCRIPT" --pack "$PACK" --project "$proj2" --apply || true)"
echo "$out" | grep -qF "'precedence'" && ok "precedence refreshed" || ko "precedence refreshed (got: $(echo "$out" | head -3))"
# Invariants custom content preserved.
grep -qF "PROJECT-SPECIFIC-MARKER-42" "$proj2/AGENTS.md" && ok "invariants preserved" || ko "invariants preserved"
# Stale text gone.
if ! grep -qF "STALE-PRECEDENCE-TEXT" "$proj2/AGENTS.md"; then
  ok "stale text replaced"
else
  ko "stale text replaced"
fi
# Fresh text back.
grep -qF "When multiple sources of advice collide" "$proj2/AGENTS.md" && ok "fresh precedence content" || ko "fresh precedence content"

# ---------------------------------------------------------------------------

printf "\n=== 3: pack introduces a new block -> appended at end with review header ===\n"
proj3="$TMP/proj3"
packmirror="$TMP/packmirror3"
mkdir -p "$proj3"
cp -R "$PACK" "$packmirror"
rm -rf "$packmirror/.git" 2>/dev/null || true
MIRROR_TPL="$packmirror/AGENTS.template.md"; [[ -f "$MIRROR_TPL" ]] || MIRROR_TPL="$packmirror/AGENTS.md"
cp "$MIRROR_TPL" "$proj3/AGENTS.md"
# Add a new pack-owned block to the mirror only.
cat >> "$MIRROR_TPL" <<'EOF'

<!-- pack-owned:begin id="brand-new-section" -->
## Brand New Section

This section is new in the pack.
<!-- pack-owned:end -->
EOF

out="$(python3 "$SCRIPT" --pack "$packmirror" --project "$proj3" --apply || true)"
echo "$out" | grep -qF "'brand-new-section'" && ok "new section inserted" || ko "new section inserted"
grep -qF "pack-owned additions (please review placement)" "$proj3/AGENTS.md" && ok "review header present" || ko "review header present"
grep -qF "Brand New Section" "$proj3/AGENTS.md" && ok "new section body written" || ko "new section body written"

# ---------------------------------------------------------------------------

printf "\n=== 4: project without markers -> refused with bootstrap hint ===\n"
proj4="$TMP/proj4"
mkdir -p "$proj4"
# Strip every pack-owned marker pair from the copy.
python3 - "$PACK_TPL" "$proj4/AGENTS.md" <<'PY'
import sys, pathlib, re
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = src.read_text(encoding="utf-8")
text = re.sub(r'^<!--\s*pack-owned:(begin|end)[^>]*-->\s*\n', '', text, flags=re.MULTILINE)
dst.write_text(text, encoding="utf-8")
PY

set +e
err="$(python3 "$SCRIPT" --pack "$PACK" --project "$proj4" 2>&1 >/dev/null)"
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "exit code 2 on pre-marker project" || ko "exit code (got $rc)"
echo "$err" | grep -qF "bootstrap migration" && ok "bootstrap hint printed" || ko "bootstrap hint printed"

# ---------------------------------------------------------------------------

printf "\n=== 5: pack dir literally named 'harmonist' -> substitution is a no-op ===\n"
proj5="$TMP/proj5"
mkdir -p "$proj5/harmonist"
cp "$PACK_TPL" "$proj5/harmonist/AGENTS.template.md"
cp "$PACK_TPL" "$proj5/AGENTS.md"
out="$(python3 "$SCRIPT" --pack "$proj5/harmonist" --project "$proj5" --apply || true)"
echo "$out" | grep -qF "already up to date" && ok "harmonist-named pack: no-op merge" || ko "harmonist-named pack: no-op merge"
grep -qF "harmonist/agents/index.json" "$proj5/AGENTS.md" \
  && ok "harmonist/ prefix left intact" || ko "harmonist/ prefix left intact"

# ---------------------------------------------------------------------------

printf "\n=== 6: pack nested at tools/harmonist -> nested prefix substituted ===\n"
proj6="$TMP/proj6"
mkdir -p "$proj6/tools/harmonist"
cp "$PACK_TPL" "$proj6/tools/harmonist/AGENTS.template.md"
cp "$PACK_TPL" "$proj6/AGENTS.md"
python3 "$SCRIPT" --pack "$proj6/tools/harmonist" --project "$proj6" --apply >/dev/null || true
if python3 - "$proj6/AGENTS.md" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert "tools/harmonist/agents/index.json" in text, "nested prefix not substituted"
# No BARE harmonist/ token may remain INSIDE pack-owned blocks (the only
# regions the merger substitutes; project-owned prose -- e.g. the template
# note explaining the substitution -- is deliberately left verbatim).
# Same lookbehind the merger uses, so tools/harmonist/ never counts.
inside = False
for n, line in enumerate(text.splitlines(), 1):
    if re.match(r"<!--\s*pack-owned:begin", line):
        inside = True
    if inside and re.search(r"(?<![\w./-])harmonist/", line):
        raise AssertionError("bare harmonist/ token remains in a pack-owned block at line %d" % n)
    if re.match(r"<!--\s*pack-owned:end", line):
        inside = False
PY
then
  ok "tools/harmonist substitution complete + no bare tokens left"
else
  ko "tools/harmonist substitution"
fi

# ---------------------------------------------------------------------------

printf "\n=== 7: replacement string with regex escapes is inserted verbatim ===\n"
# A pack dir containing re.sub escapes (\1, \g<0>) must not be interpreted
# as backrefs (replacement-string injection).
if python3 - "$(dirname "$SCRIPT")" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from merge_agents_md import substitute_pack_dir
out = substitute_pack_dir("see harmonist/agents/index.json", r"weird\1\g<0>dir")
assert r"weird\1\g<0>dir/agents/index.json" in out, out
PY
then
  ok "regex-escape pack dir lands verbatim (no backref expansion)"
else
  ko "regex-escape pack dir mangled"
fi

# ---------------------------------------------------------------------------

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
