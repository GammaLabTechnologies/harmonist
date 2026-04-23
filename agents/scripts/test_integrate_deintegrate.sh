#!/usr/bin/env bash
# Tests for integrate.py + deintegrate.py (F2/F3) and build_index.py
# freshness-field emission (F1).

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INT="$PACK/agents/scripts/integrate.py"
DEINT="$PACK/agents/scripts/deintegrate.py"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# F1: build_index.py emits version/updated_at/deprecated
# ---------------------------------------------------------------------------

printf "\n=== F1: index.json carries version fields ===\n"
python3 -c "
import json
d = json.load(open('$PACK/agents/index.json'))
counts = d['counts']
assert 'with_version' in counts, 'counts.with_version missing'
assert 'with_updated_at' in counts, 'counts.with_updated_at missing'
assert 'deprecated' in counts, 'counts.deprecated missing'
assert counts['with_version'] >= 8, f'too few versioned agents: {counts[\"with_version\"]}'
# Find an agent with version and check the field is emitted.
for a in d['agents']:
    if a.get('version'):
        assert a.get('updated_at'), f'{a[\"slug\"]} has version but no updated_at'
        assert 'deprecated' in a, f'{a[\"slug\"]} missing deprecated field'
        break
else:
    raise AssertionError('no agent with version found')
" && ok "index emits version/updated_at/deprecated + counts" || ko "index missing fields"

# ---------------------------------------------------------------------------
# F2: integrate.py dry-run on a blank project
# ---------------------------------------------------------------------------

printf "\n=== F2: integrate --dry-run on blank project ===\n"
blank="$TMP/blank"
mkdir -p "$blank"
set +e
out="$(python3 "$INT" --project "$blank" --pack "$PACK" --dry-run 2>&1)"
rc=$?
set -e
printf '%s' "$out" | /usr/bin/grep -q "Integration plan" && ok "dry-run renders a plan" || ko "no plan output"
printf '%s' "$out" | /usr/bin/grep -q "preflight" && ok "plan includes preflight step" || ko "no preflight"
printf '%s' "$out" | /usr/bin/grep -q "Next steps for the team" && ok "manual follow-ups listed" || ko "no next-steps"

# Dry-run never writes.
[[ ! -d "$blank/.cursor" ]] && ok "dry-run created no .cursor/" || ko "dry-run wrote .cursor/"

# ---------------------------------------------------------------------------
# F2: integrate --apply on a blank project
# ---------------------------------------------------------------------------

printf "\n=== F2: integrate --apply ===\n"
proj="$TMP/proj"
mkdir -p "$proj"
set +e
python3 "$INT" --project "$proj" --pack "$PACK" --skip-smoke > "$TMP/int.out" 2>&1
rc=$?
set -e
[[ "$rc" == "0" || "$rc" == "1" ]] && ok "integrate --apply completed (exit $rc)" || ko "integrate exit=$rc: $(tail -5 $TMP/int.out)"

# Pack-owned files landed.
[[ -f "$proj/AGENTS.md" ]] && ok "AGENTS.md installed" || ko "AGENTS.md missing"
[[ -f "$proj/.cursor/hooks.json" ]] && ok "hooks.json installed" || ko "hooks.json missing"
[[ -f "$proj/.cursor/agents/qa-verifier.md" ]] && ok "strict reviewer installed" || ko "qa-verifier missing"
[[ -f "$proj/.cursor/rules/protocol-enforcement.mdc" ]] && ok "protocol-enforcement rule installed" || ko "protocol rule missing"
[[ -f "$proj/.cursor/rules/project-domain-rules.mdc" ]] && ok "project-domain-rules skeleton installed" || ko "domain-rules skeleton missing"
[[ -f "$proj/.cursor/memory/session-handoff.md" ]] && ok "memory installed" || ko "memory missing"
[[ -f "$proj/.cursor/pack-version.json" ]] && ok "pack-version recorded" || ko "pack-version missing"

# Session-handoff received the bootstrap entry.
/usr/bin/grep -q "Integration complete" "$proj/.cursor/memory/session-handoff.md" \
  && ok "session-handoff has bootstrap entry" \
  || ko "no bootstrap entry"

# ---------------------------------------------------------------------------
# F2: integrate is idempotent (re-run on same project)
# ---------------------------------------------------------------------------

printf "\n=== F2: idempotent re-run ===\n"
set +e
python3 "$INT" --project "$proj" --pack "$PACK" --skip-smoke > "$TMP/int2.out" 2>&1
rc2=$?
set -e
[[ "$rc2" == "0" || "$rc2" == "1" ]] && ok "second integrate exits cleanly" || ko "second integrate exit=$rc2"

# Bootstrap entry should NOT have been duplicated (memory already has real entries).
boot_count="$(/usr/bin/grep -c "Integration complete" "$proj/.cursor/memory/session-handoff.md")"
[[ "$boot_count" == "1" ]] && ok "bootstrap entry is not duplicated" || ko "bootstrap duplicated ($boot_count)"

# ---------------------------------------------------------------------------
# F2: --json output is machine-parseable
# ---------------------------------------------------------------------------

printf "\n=== F2: --json shape ===\n"
json_out="$(python3 "$INT" --project "$proj" --pack "$PACK" --dry-run --json 2>&1)"
if printf '%s' "$json_out" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'steps' in d and isinstance(d['steps'], list)
assert any(s['name'] == 'preflight' for s in d['steps'])
assert 'ok' in d
"; then
  ok "JSON payload has expected shape"
else
  ko "JSON malformed"
fi

# ---------------------------------------------------------------------------
# F3: deintegrate --dry-run
# ---------------------------------------------------------------------------

printf "\n=== F3: deintegrate dry-run ===\n"
set +e
out="$(python3 "$DEINT" --project "$proj" 2>&1)"
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "dry-run exits 0" || ko "dry-run exit=$rc"
printf '%s' "$out" | /usr/bin/grep -q "dry-run: nothing deleted" && ok "warns dry-run" || ko "no dry-run warning"
[[ -d "$proj/.cursor" ]] && ok "dry-run preserved .cursor/" || ko "dry-run deleted"

# ---------------------------------------------------------------------------
# F3: deintegrate --apply removes pack files + creates snapshot
# ---------------------------------------------------------------------------

printf "\n=== F3: deintegrate --apply ===\n"
set +e
python3 "$DEINT" --project "$proj" --apply > "$TMP/deint.out" 2>&1
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "deintegrate --apply exits 0" || ko "deintegrate exit=$rc"

[[ ! -f "$proj/.cursor/hooks.json" ]] && ok "hooks.json removed" || ko "hooks.json still present"
[[ ! -f "$proj/.cursor/agents/qa-verifier.md" ]] && ok "strict reviewer removed" || ko "qa-verifier still present"
[[ ! -f "$proj/AGENTS.md" ]] && ok "AGENTS.md removed" || ko "AGENTS.md still present"

# Memory should remain by default (user data).
[[ -f "$proj/.cursor/memory/session-handoff.md" ]] && ok "memory entries preserved (user data)" || ko "memory purged without --purge-memory"

# Snapshot was created for rollback.
snap_count="$(ls "$proj/.cursor/.integration-snapshots/pre-deintegrate-"*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
[[ "$snap_count" -ge "1" ]] && ok "pre-deintegrate snapshot created" || ko "no snapshot"

# ---------------------------------------------------------------------------
# F3: deintegrate on a project with no .cursor/ exits 1
# ---------------------------------------------------------------------------

printf "\n=== F3: deintegrate on non-integrated project ===\n"
none="$TMP/none"
mkdir -p "$none"
set +e
python3 "$DEINT" --project "$none" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "returns 1 on non-integrated project" || ko "rc=$rc"

# ---------------------------------------------------------------------------
# F3: --keep-agents-md preserves AGENTS.md
# ---------------------------------------------------------------------------

printf "\n=== F3: --keep-agents-md ===\n"
proj2="$TMP/proj2"
mkdir -p "$proj2"
python3 "$INT" --project "$proj2" --pack "$PACK" --skip-smoke >/dev/null 2>&1 || true
echo "# my custom invariants" >> "$proj2/AGENTS.md"
python3 "$DEINT" --project "$proj2" --apply --keep-agents-md >/dev/null 2>&1 || true
[[ -f "$proj2/AGENTS.md" ]] && ok "AGENTS.md preserved with --keep-agents-md" || ko "AGENTS.md deleted despite --keep-agents-md"

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
