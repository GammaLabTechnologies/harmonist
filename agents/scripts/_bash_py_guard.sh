# shellcheck shell=bash
# Python version guard for bash entry points.
#
# Usage (at the top of any script that invokes python3):
#
#   source "$(dirname "${BASH_SOURCE[0]}")/_bash_py_guard.sh"
#   require_python_39   # aborts with a helpful message if python3 is < 3.9
#
# The guard is kept in sync with the Python-side guard in
# _py_guard_snippet.py: both demand 3.9+.

require_python_39() {
  local py="${PYTHON:-python3}"
  if ! command -v "$py" >/dev/null 2>&1; then
    cat >&2 <<EOF
error: no \`$py\` on PATH.
harmonist needs Python 3.9 or newer.
  macOS:   brew install python@3.12 && hash -r
  Ubuntu:  sudo apt install python3.12 python3.12-venv
  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0
Then set:  export PYTHON=python3.12
EOF
    return 3
  fi
  if ! "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
    local cur
    cur="$("$py" -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))' 2>/dev/null || echo "?.?")"
    cat >&2 <<EOF
error: \`$py\` is Python $cur; harmonist requires 3.9+.
  macOS:   brew install python@3.12 && hash -r
  Ubuntu:  sudo apt install python3.12 python3.12-venv
  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0
Then set:  export PYTHON=python3.12   (or point \$PYTHON at a newer interpreter)
EOF
    return 3
  fi
  # Export so downstream `python3` invocations in the same script can switch
  # to the caller's chosen interpreter if they respect \$PYTHON.
  export PYTHON="$py"
  return 0
}
