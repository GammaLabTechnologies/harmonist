#!/usr/bin/env python3
"""
test_cross_platform.py -- OS-agnostic regression for the install path.

Unlike the bash test suites (which need a POSIX shell), this runner uses
only the Python stdlib, so it exercises the exact code Windows users run:
convert.py, install.py, the hooks.json interpreter render, and a real
upgrade + smoke_test end-to-end. Runs identically on Windows, macOS, Linux.

Usage:
    python3 agents/scripts/test_cross_platform.py
    python3 agents/scripts/test_cross_platform.py -v

Exit codes:
    0 = all checks passed
    1 = one or more checks failed
"""

from __future__ import annotations

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

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK_ROOT = HERE.parent.parent

_passed = 0
_failed = 0
_verbose = False


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        if _verbose:
            print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def _load_upgrade_module():
    spec = importlib.util.spec_from_file_location("upg_xp", HERE / "upgrade.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["upg_xp"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_convert(tmp: Path) -> None:
    out = tmp / "int"
    r = subprocess.run([sys.executable, str(HERE / "convert.py"),
                        "--tool", "cursor", "--out", str(out)],
                       capture_output=True, text=True)
    check("convert.py --tool cursor exits 0", r.returncode == 0, r.stderr[-200:])
    mdc = list((out / "cursor" / "rules").glob("*.mdc")) if (out / "cursor" / "rules").is_dir() else []
    check("convert.py produced .mdc rules", len(mdc) >= 100, f"found {len(mdc)}")
    if mdc:
        check("rendered rule is non-empty", mdc[0].stat().st_size > 0)


def test_install(tmp: Path) -> None:
    # install.py copies from the pack's own integrations/ dir, so build the
    # cursor integration there first, then install into a temp project cwd
    # (cursor is project-scoped -> nothing global is touched).
    subprocess.run([sys.executable, str(HERE / "convert.py"), "--tool", "cursor"],
                   capture_output=True, text=True)
    proj = tmp / "inst_proj"
    proj.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, str(HERE / "install.py"),
                        "--tool", "cursor", "--no-interactive"],
                       capture_output=True, text=True, cwd=str(proj))
    check("install.py --tool cursor exits 0", r.returncode == 0, r.stderr[-200:])
    rules_dir = proj / ".cursor" / "rules"
    rules = list(rules_dir.glob("*.mdc")) if rules_dir.is_dir() else []
    check("install.py populated .cursor/rules", len(rules) >= 100, f"found {len(rules)}")


def test_hooks_render() -> None:
    upg = _load_upgrade_module()
    out = upg.render_hooks_json(PACK_ROOT / "hooks" / "hooks.json", "py -3")
    data = json.loads(out)
    cmds = [e["command"] for entries in data["hooks"].values() for e in entries]
    check("render uses the given interpreter (py -3)",
          all(c.startswith("py -3 .cursor/hooks/scripts/hook_runner.py") for c in cmds))
    check("render preserves loop_limit", data["hooks"]["stop"][0].get("loop_limit") == 3)
    interp = upg.detect_hook_interpreter()
    check("detect_hook_interpreter returns something", bool(interp), repr(interp))


def test_repomap(tmp: Path) -> None:
    proj = tmp / "rm"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "tests").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "fee.py").write_text("def calc_fee(a):\n    return a*0.03\n")
    (proj / "src" / "billing.py").write_text(
        "from src.fee import calc_fee\ndef charge(x):\n    return calc_fee(x)\n")
    (proj / "tests" / "test_billing.py").write_text(
        "from src.billing import charge\ndef test_charge():\n    assert charge(100)==3.0\n")
    rm = HERE / "repomap.py"
    b = subprocess.run([sys.executable, str(rm), "build", "--project", str(proj), "--json"],
                       capture_output=True, text=True)
    check("repomap build exits 0", b.returncode == 0, b.stderr[-200:])
    try:
        edges = json.loads(b.stdout).get("edges", 0)
    except Exception:
        edges = 0
    check("repomap resolved import edges", edges >= 2, f"edges={edges}")
    imp = subprocess.run([sys.executable, str(rm), "impact", "src/fee.py",
                          "--project", str(proj), "--json"], capture_output=True, text=True)
    try:
        blast = json.loads(imp.stdout or "[]")
    except Exception:
        blast = []
    check("repomap impact reaches billing", "src/billing.py" in blast, str(blast))
    aff = subprocess.run([sys.executable, str(rm), "affected", "src/fee.py",
                          "--project", str(proj), "--json"], capture_output=True, text=True)
    try:
        tests = json.loads(aff.stdout or "[]")
    except Exception:
        tests = []
    check("repomap affected finds the test", "tests/test_billing.py" in tests, str(tests))


def test_e2e_integrate_smoke(tmp: Path) -> None:
    proj = tmp / "e2e"
    proj.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACK_ROOT / "AGENTS.md", proj / "AGENTS.md")
    r = subprocess.run([sys.executable, str(HERE / "upgrade.py"),
                        "--project", str(proj), "--pack", str(PACK_ROOT), "--apply"],
                       capture_output=True, text=True)
    check("upgrade.py --apply succeeds", r.returncode in (0, 1), r.stderr[-200:])
    runner = proj / ".cursor" / "hooks" / "scripts" / "hook_runner.py"
    check("hook_runner.py installed into project", runner.exists())
    hooks_json = proj / ".cursor" / "hooks.json"
    check("hooks.json installed", hooks_json.exists())
    if hooks_json.exists():
        data = json.loads(hooks_json.read_text())
        cmd = data["hooks"]["stop"][0]["command"]
        check("hooks.json stop cmd targets hook_runner.py",
              "hook_runner.py stop" in cmd, cmd)
    # The real cross-OS proof: smoke_test drives hook_runner.py end-to-end.
    s = subprocess.run([sys.executable, str(HERE / "smoke_test.py"),
                        "--project", str(proj), "--json"],
                       capture_output=True, text=True)
    ok = s.returncode == 0
    detail = ""
    if not ok:
        detail = (s.stderr or s.stdout)[-200:]
    check("smoke_test (Python hook runner) passes end-to-end", ok, detail)


def main(argv: list[str]) -> int:
    global _verbose
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _verbose = args.verbose

    print(f"cross-platform suite (os={os.name}, py={sys.version_info.major}.{sys.version_info.minor})")
    with tempfile.TemporaryDirectory(prefix="asp-xp-") as td:
        tmp = Path(td)
        print("\n=== convert.py ===")
        test_convert(tmp)
        print("\n=== install.py ===")
        test_install(tmp)
        print("\n=== hooks.json interpreter render ===")
        test_hooks_render()
        print("\n=== repo map (build / impact / affected) ===")
        test_repomap(tmp)
        print("\n=== e2e: upgrade --apply + smoke_test ===")
        test_e2e_integrate_smoke(tmp)

    print("")
    print(f"  passed: {_passed}  failed: {_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
