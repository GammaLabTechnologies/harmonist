#!/usr/bin/env bash
#
# convert.sh -- POSIX wrapper around the cross-platform convert.py.
#
# The conversion logic now lives in convert.py so it runs identically on
# macOS, Linux, and native Windows. This wrapper is kept for POSIX muscle
# memory, shell pipelines, and CI. On Windows, run `python convert.py`
# (or `py -3 convert.py`) directly.
#
# All arguments are forwarded verbatim:
#   ./scripts/convert.sh [--tool <name>] [--out <dir>] [--thin] [--parallel] [--jobs N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=agents/scripts/_bash_py_guard.sh
source "$SCRIPT_DIR/_bash_py_guard.sh"
require_python_39 || exit $?

exec "${PYTHON:-python3}" "$SCRIPT_DIR/convert.py" "$@"
