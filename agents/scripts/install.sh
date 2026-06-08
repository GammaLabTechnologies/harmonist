#!/usr/bin/env bash
#
# install.sh -- POSIX wrapper around the cross-platform install.py.
#
# The install logic now lives in install.py so it runs identically on
# macOS, Linux, and native Windows. This wrapper is kept for POSIX muscle
# memory and CI. On Windows, run `python install.py` (or `py -3 install.py`)
# directly.
#
# All arguments are forwarded verbatim:
#   ./scripts/install.sh [--tool <name>] [--interactive] [--no-interactive] [--parallel] [--jobs N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=agents/scripts/_bash_py_guard.sh
source "$SCRIPT_DIR/_bash_py_guard.sh"
require_python_39 || exit $?

exec "${PYTHON:-python3}" "$SCRIPT_DIR/install.py" "$@"
