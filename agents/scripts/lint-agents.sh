#!/usr/bin/env bash
#
# lint-agents.sh — validate that every agent conforms to Schema v2.
#
# Delegates to the Python linter in lint_agents.py, which reuses the parser
# from migrate_schema.py. Kept as a shell wrapper so existing CI invocations
# (./scripts/lint-agents.sh) keep working.
#
# Usage:
#   ./scripts/lint-agents.sh             # lint every agent under agents/
#   ./scripts/lint-agents.sh path/to.md  # lint specific file(s)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=agents/scripts/_bash_py_guard.sh
source "$SCRIPT_DIR/_bash_py_guard.sh"
require_python_39 || exit $?

exec "${PYTHON:-python3}" "$SCRIPT_DIR/lint_agents.py" "$@"
