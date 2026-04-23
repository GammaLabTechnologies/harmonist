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
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0
fail=0

ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------

printf "\n=== 1: dry-run on a verbatim pack copy -> nothing changes ===\n"
proj1="$TMP/proj1"
mkdir -p "$proj1"
cp "$PACK/AGENTS.md" "$proj1/AGENTS.md"
out="$(python3 "$SCRIPT" --pack "$PACK" --project "$proj1" || true)"
echo "$out" | grep -qF "already up to date" && ok "reports up-to-date" || ko "reports up-to-date"

# ---------------------------------------------------------------------------

printf "\n=== 2: customised project, stale pack block -> refresh only the pack block ===\n"
proj2="$TMP/proj2"
mkdir -p "$proj2"
cp "$PACK/AGENTS.md" "$proj2/AGENTS.md"
# Customise a project-owned section (Invariants) + corrupt a pack-owned one (Precedence).
python3 - "$proj2/AGENTS.md" <<'PY'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1])
text = p.read_text()
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
p.write_text(text)
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
cp "$packmirror/AGENTS.md" "$proj3/AGENTS.md"
# Add a new pack-owned block to the mirror only.
cat >> "$packmirror/AGENTS.md" <<'EOF'

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
python3 - "$PACK/AGENTS.md" "$proj4/AGENTS.md" <<'PY'
import sys, pathlib, re
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = src.read_text()
text = re.sub(r'^<!--\s*pack-owned:(begin|end)[^>]*-->\s*\n', '', text, flags=re.MULTILINE)
dst.write_text(text)
PY

set +e
err="$(python3 "$SCRIPT" --pack "$PACK" --project "$proj4" 2>&1 >/dev/null)"
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "exit code 2 on pre-marker project" || ko "exit code (got $rc)"
echo "$err" | grep -qF "bootstrap migration" && ok "bootstrap hint printed" || ko "bootstrap hint printed"

# ---------------------------------------------------------------------------

echo ""
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
