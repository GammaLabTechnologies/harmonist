# --- python-version guard ---------------------------------------------------
# This snippet is copied verbatim into the top of every entry script that
# needs Python 3.9+ (for `str.removesuffix`, `typing` under
# `from __future__ import annotations`, `argparse.BooleanOptionalAction`,
# `dict[str, Any]` at runtime via future-annotations, etc.).
#
# Keep the snippet syntactically valid on Python 2.7 AND 3.0+. No f-strings,
# no walrus, no walrus operator, no type hints -- otherwise the user will
# see `SyntaxError` BEFORE the guard runs and the whole point is lost.
#
# To refresh the snippet in every script, run:
#   python3 agents/scripts/refresh_py_guard.py
#
# Canonical text below. EVERYTHING between the BEGIN/END markers (including
# the markers) is rewritten by the refresher; edit here, never inline.
# === PY-GUARD:BEGIN ===
import sys as _asp_sys
if _asp_sys.version_info < (3, 9):
    _asp_cur = "%d.%d" % (_asp_sys.version_info[0], _asp_sys.version_info[1])
    _asp_sys.stderr.write(
        "harmonist requires Python 3.9+ (found " + _asp_cur + ").\n"
        "Install a modern Python and retry:\n"
        "  macOS:   brew install python@3.12 && hash -r\n"
        "  Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
        "  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0\n"
        "Then:     python3 " + _asp_sys.argv[0] + "\n"
    )
    _asp_sys.exit(3)
# === PY-GUARD:END ===
