#!/usr/bin/env bash
# Installs the harmonist git pre-commit guard into .git/hooks.
#
# The guard logic lives in .cursor/hooks/scripts/git-pre-commit.sh (tracked in
# git, reviewable). .git/hooks/pre-commit is a thin shim that execs it, so
# policy updates ship via the repo, not via untracked .git state.
#
# Re-runnable (idempotent). Backs up any pre-existing non-ours pre-commit.
# Does NOT modify git config (no core.hooksPath change). Run from the project
# root after integration:
#   bash .cursor/hooks/scripts/install-git-hooks.sh
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer the tracked copy under .cursor/; fall back to this script's own dir
# (e.g. running from the pack before integration).
if [[ -f "$REPO_ROOT/.cursor/hooks/scripts/git-pre-commit.sh" ]]; then
  HOOK_SRC="$REPO_ROOT/.cursor/hooks/scripts/git-pre-commit.sh"
else
  HOOK_SRC="$SCRIPT_DIR/git-pre-commit.sh"
fi
HOOKS_DIR="$(git rev-parse --git-path hooks)"
HOOK_DST="$HOOKS_DIR/pre-commit"
MARKER="harmonist-enforcement-precommit"

[[ -f "$HOOK_SRC" ]] || { echo "error: missing $HOOK_SRC" >&2; exit 1; }
mkdir -p "$HOOKS_DIR"
chmod +x "$HOOK_SRC" 2>/dev/null || true

if [[ -e "$HOOK_DST" ]] && ! grep -q "$MARKER" "$HOOK_DST" 2>/dev/null; then
  backup="$HOOK_DST.bak.$(date +%s)"
  cp "$HOOK_DST" "$backup"
  echo "backed up existing pre-commit -> $backup"
fi

cat > "$HOOK_DST" <<EOF
#!/usr/bin/env bash
# $MARKER (installed shim) -- delegates to the tracked guard.
exec "$HOOK_SRC" "\$@"
EOF
chmod +x "$HOOK_DST"
echo "installed pre-commit guard -> $HOOK_DST"
echo "  (logic: $HOOK_SRC ; bypass: git commit --no-verify)"
