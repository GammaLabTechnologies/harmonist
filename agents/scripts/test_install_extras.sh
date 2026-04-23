#!/usr/bin/env bash
# Tests for scripts/install_extras.py. Covers happy paths (slug / role /
# tag / thin), safety rails (strict refusal, sha mismatch), and the
# pack-manifest merge contract.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INT="$PACK/agents/scripts/integrate.py"
SCRIPT="$PACK/agents/scripts/install_extras.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# Prep: one freshly-integrated project we can reuse across scenarios.
# ---------------------------------------------------------------------------
proj="$TMP/proj"
mkdir -p "$proj"
# integrate.py exits 0 on clean plan / 1 when it copied files + surfaced
# post-integration guidance. Both mean the target was populated.
set +e
python3 "$INT" --project "$proj" --pack "$PACK" --skip-smoke > "$TMP/int.out" 2>&1
set -e
[[ -d "$proj/.cursor/agents" ]] || { echo "FATAL: no .cursor/agents after integrate"; tail -20 "$TMP/int.out"; exit 1; }
# Strict agents must be present (the fixture we'll exercise against).
[[ -f "$proj/.cursor/agents/qa-verifier.md" ]] || { echo "FATAL: qa-verifier missing after integrate"; exit 1; }

# ---------------------------------------------------------------------------
# 1. --help renders
# ---------------------------------------------------------------------------
printf "\n=== 1: --help renders ===\n"
out="$(python3 "$SCRIPT" --help 2>&1)" || true
printf '%s' "$out" | grep -q 'Install extra specialists' && ok "help mentions purpose" || ko "help missing purpose"
printf '%s' "$out" | grep -q -- '--role' && ok "help lists --role" || ko "no --role"
printf '%s' "$out" | grep -q -- '--thin' && ok "help lists --thin" || ko "no --thin"

# ---------------------------------------------------------------------------
# 2. --list mode without any filter prints zero candidates (expected: must
#    be explicit about what to browse; empty candidate list is the correct
#    terminal behaviour, not a crash)
# ---------------------------------------------------------------------------
printf "\n=== 2: --list with --role prints that role's candidates ===\n"
out="$(python3 "$SCRIPT" --project "$proj" --pack "$PACK" --list --role design 2>&1)"
printf '%s' "$out" | grep -q 'design-ux-architect' && ok "--list --role design includes design-ux-architect" || ko "design-ux-architect missing"
printf '%s' "$out" | grep -q '+ design-ux-architect\| - design-ux-architect' \
  && ok "--list shows install-state marker" || ko "no state marker"

# ---------------------------------------------------------------------------
# 3. --slug dry-run writes nothing
# ---------------------------------------------------------------------------
printf "\n=== 3: --slug --dry-run writes nothing ===\n"
before="$(ls "$proj/.cursor/agents" | sort)"
out="$(python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
         --slug design-ux-architect --dry-run 2>&1)"
after="$(ls "$proj/.cursor/agents" | sort)"
[[ "$before" == "$after" ]] && ok "dry-run did not change .cursor/agents/" || ko "dry-run modified directory"
printf '%s' "$out" | grep -q 'mode: DRY-RUN' && ok "dry-run mode banner present" || ko "no banner"
printf '%s' "$out" | grep -q '? design-ux-architect' && ok "candidate listed with ? marker" || ko "no candidate row"

# ---------------------------------------------------------------------------
# 4. --slug apply installs + pack-manifest merged
# ---------------------------------------------------------------------------
printf "\n=== 4: --slug apply copies + updates pack-manifest.json ===\n"
python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
    --slug design-ux-architect,product-manager > "$TMP/apply.out" 2>&1 \
    && rc=0 || rc=$?
[[ "$rc" == "0" ]] && ok "apply exits 0" || ko "apply exit=$rc: $(tail -5 $TMP/apply.out)"
[[ -f "$proj/.cursor/agents/design-ux-architect.md" ]] && ok "design-ux-architect installed" || ko "missing"
[[ -f "$proj/.cursor/agents/product-manager.md" ]]     && ok "product-manager installed"     || ko "missing"
# Pack-manifest merged.
python3 -c "
import json, sys
pm = json.load(open('$proj/.cursor/pack-manifest.json'))
files = pm.get('files', {})
assert any('design-ux-architect' in k for k in files), 'design-ux-architect not in pack-manifest.files'
assert any('product-manager' in k for k in files), 'product-manager not in pack-manifest.files'
print('OK')
" && ok "pack-manifest.json contains new slugs" || ko "pack-manifest not updated"

# ---------------------------------------------------------------------------
# 5. Re-running the same slug without --force skips (idempotent)
# ---------------------------------------------------------------------------
printf "\n=== 5: re-run without --force skips existing ===\n"
out="$(python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
          --slug design-ux-architect 2>&1)"
printf '%s' "$out" | grep -q '= design-ux-architect' && ok "re-run emits skip-exists row" || ko "no skip row"
printf '%s' "$out" | grep -q 'already installed' && ok "reason mentions 'already installed'" || ko "no reason"

# ---------------------------------------------------------------------------
# 6. --force overwrites existing
# ---------------------------------------------------------------------------
printf "\n=== 6: --force overwrites ===\n"
# Mutate the installed file so we can confirm overwrite actually happened.
echo "<!-- USER-EDITED -->" >> "$proj/.cursor/agents/design-ux-architect.md"
out="$(python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
          --slug design-ux-architect --force 2>&1)"
grep -q 'USER-EDITED' "$proj/.cursor/agents/design-ux-architect.md" \
  && ko "user edit survived --force (should be gone)" \
  || ok "--force wiped user edit"
printf '%s' "$out" | grep -q '+ design-ux-architect' && ok "--force emits copy row" || ko "no copy row under --force"

# ---------------------------------------------------------------------------
# 7. --role installs the bundle
# ---------------------------------------------------------------------------
printf "\n=== 7: --role marketing installs the default bundle ===\n"
python3 "$SCRIPT" --project "$proj" --pack "$PACK" --role marketing \
    > "$TMP/role.out" 2>&1 && rc=0 || rc=$?
[[ "$rc" == "0" ]] && ok "role install exits 0" || ko "role install exit=$rc"
# ROLE_DEFAULTS.marketing = [seo, content, growth]
[[ -f "$proj/.cursor/agents/marketing-seo-specialist.md" ]] && ok "marketing-seo-specialist installed" || ko "seo missing"
[[ -f "$proj/.cursor/agents/marketing-content-creator.md" ]] && ok "marketing-content-creator installed" || ko "content missing"
[[ -f "$proj/.cursor/agents/marketing-growth-hacker.md" ]] && ok "marketing-growth-hacker installed"   || ko "growth missing"

# ---------------------------------------------------------------------------
# 8. --role <unknown> exits 2 with a helpful message
# ---------------------------------------------------------------------------
printf "\n=== 8: unknown role -> exit 2 ===\n"
set +e
python3 "$SCRIPT" --project "$proj" --pack "$PACK" --role WAT > "$TMP/unknown.out" 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "unknown role exits 2" || ko "unknown role exit=$rc"
grep -q "Known role bundles" "$TMP/unknown.out" && ok "error message lists known roles" || ko "no list in error"

# ---------------------------------------------------------------------------
# 9. --tag requires ALL specified tags by default
# ---------------------------------------------------------------------------
printf "\n=== 9: --tag filter installs matching agents ===\n"
python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
    --tag qa,regression --dry-run > "$TMP/tag.out" 2>&1 || true
# Any matches at all => success (exact set depends on catalog state; this
# is a smoke test for the tag path, not an equivalence check).
grep -q '? ' "$TMP/tag.out" && ok "--tag produced at least one candidate" || ko "no candidates from --tag"

# ---------------------------------------------------------------------------
# 10. --thin produces a thin variant without Deep Reference body
# ---------------------------------------------------------------------------
printf "\n=== 10: --thin installs an essentials-only body ===\n"
# Remove any prior install of the target.
rm -f "$proj/.cursor/agents/testing-accessibility-auditor.md"
python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
    --slug testing-accessibility-auditor --thin > "$TMP/thin.out" 2>&1 \
    && rc=0 || rc=$?
[[ "$rc" == "0" ]] && ok "thin install exits 0" || ko "thin install exit=$rc"
[[ -f "$proj/.cursor/agents/testing-accessibility-auditor.md" ]] \
  && ok "thin file written" || ko "thin file missing"
# The thin cut must NOT contain the full Deep Reference content. The source
# file has `## Deep Reference` then templates below; thin variant contains
# everything ABOVE the marker but not below.
src_lines=$(wc -l < "$PACK/agents/testing/testing-accessibility-auditor.md" | tr -d ' ')
thin_lines=$(wc -l < "$proj/.cursor/agents/testing-accessibility-auditor.md" | tr -d ' ')
(( thin_lines < src_lines )) && ok "thin body ($thin_lines lines) < full ($src_lines lines)" \
                             || ko "thin body ($thin_lines) is not smaller than full ($src_lines)"
# pack-manifest records the thin marker.
grep -q '(thin)' "$proj/.cursor/pack-manifest.json" && ok "pack-manifest flags thin entries" || ko "no (thin) tag in pack-manifest"

# ---------------------------------------------------------------------------
# 11. Strict slug is refused
# ---------------------------------------------------------------------------
printf "\n=== 11: --slug qa-verifier refused (strict) ===\n"
# qa-verifier is already installed by integrate. Force-attempt should still
# be refused because the script never owns strict slugs.
set +e
python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
    --slug qa-verifier --force > "$TMP/strict.out" 2>&1
rc=$?
set -e
grep -q '! qa-verifier' "$TMP/strict.out" && ok "qa-verifier emits refusal row" || ko "no refusal row"
grep -q 'strict/orchestration' "$TMP/strict.out" && ok "reason names strict/orchestration" || ko "reason missing"
[[ "$rc" == "2" ]] && ok "refuse-only returns exit 2" || ko "exit=$rc for refuse-only"

# ---------------------------------------------------------------------------
# 12. Unknown slug is refused with a clear row
# ---------------------------------------------------------------------------
printf "\n=== 12: unknown slug refused with index-lookup reason ===\n"
set +e
python3 "$SCRIPT" --project "$proj" --pack "$PACK" \
    --slug this-agent-does-not-exist > "$TMP/unknown-slug.out" 2>&1
rc=$?
set -e
grep -q 'unknown slugs' "$TMP/unknown-slug.out" && ok "warns about unknown slugs" || ko "no unknown-slugs warn"

# ---------------------------------------------------------------------------
# 13. Non-integrated project (no .cursor/agents/) fails fast (unless --list)
# ---------------------------------------------------------------------------
printf "\n=== 13: non-integrated project fails fast ===\n"
raw="$TMP/raw"
mkdir -p "$raw"
set +e
python3 "$SCRIPT" --project "$raw" --pack "$PACK" \
    --slug design-ui-designer > "$TMP/raw.out" 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "non-integrated exits 2" || ko "expected 2, got $rc"
grep -q '.cursor/agents/\` is missing' "$TMP/raw.out" && ok "error explains missing directory" \
  || grep -q '.cursor/agents' "$TMP/raw.out" && ok "error mentions .cursor/agents" \
  || ko "no helpful error message"

# ---------------------------------------------------------------------------
# 14. Sha-tampering guard: mutate a source file, confirm REFUSED
# ---------------------------------------------------------------------------
printf "\n=== 14: tampered source file is REFUSED ===\n"
# Clone the pack to a sandbox so we can mutate without disturbing the real one.
fakepack="$TMP/fakepack"
mkdir -p "$fakepack"
cp -R "$PACK/agents"    "$fakepack/agents"
cp -R "$PACK/memory"    "$fakepack/memory"
cp -R "$PACK/hooks"     "$fakepack/hooks"
cp    "$PACK/VERSION"   "$fakepack/"
cp    "$PACK/MANIFEST.sha256" "$fakepack/"
cp    "$PACK/AGENTS.md" "$fakepack/"
# Mutate one source — sha will no longer match MANIFEST.
echo "<!-- tampered -->" >> "$fakepack/agents/design/design-brand-guardian.md"
# Fresh project so the file isn't already installed.
proj2="$TMP/proj2"
mkdir -p "$proj2"
set +e
python3 "$INT" --project "$proj2" --pack "$PACK" --skip-smoke > "$TMP/int2.out" 2>&1
set -e
[[ -d "$proj2/.cursor/agents" ]] || { echo "FATAL: int2 fixture missing"; exit 1; }
set +e
python3 "$SCRIPT" --project "$proj2" --pack "$fakepack" \
    --slug design-brand-guardian > "$TMP/tamper.out" 2>&1
rc=$?
set -e
grep -q '! design-brand-guardian' "$TMP/tamper.out" && ok "tampered source refused" \
                                                     || ko "tampered source NOT refused"
grep -q 'MANIFEST expected' "$TMP/tamper.out" && ok "reason names sha mismatch" || ko "no sha mention"
[[ ! -f "$proj2/.cursor/agents/design-brand-guardian.md" ]] \
  && ok "tampered file NOT copied" \
  || ko "tampered file was copied (supply-chain breach)"

# ---------------------------------------------------------------------------
# 15. pack-manifest.json keeps strict-agent entries intact after merge
# ---------------------------------------------------------------------------
printf "\n=== 15: merge preserves existing pack-manifest entries ===\n"
python3 -c "
import json
pm = json.load(open('$proj/.cursor/pack-manifest.json'))
files = pm.get('files', {})
strict_present = any('qa-verifier' in k or 'security-reviewer' in k for k in files)
assert strict_present, 'strict-agent entries were wiped by merge'
print('OK')
" && ok "strict-agent sha entries survived merge" || ko "strict entries lost"

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
printf "\n=== summary ===\n"
printf "  passed: %d  failed: %d\n" "$pass" "$fail"
(( fail == 0 )) || exit 1
