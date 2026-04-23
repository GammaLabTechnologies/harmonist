#!/usr/bin/env python3
"""
run_regression.py -- actually run a project's regression commands
(test / lint / typecheck / build) and record the outcome in the
enforcement hook state.

Until today, `bg-regression-runner` was a persona agent whose output
was LLM-interpreted. The stop-gate only checked that the reviewer was
invoked, not whether tests actually passed. This script closes that
gap: it shells out to the real commands, captures exit codes + stderr
tails, and writes the structured result so gate-stop.sh (and manual
review) can see the ground truth.

Usage:
    # Run the four detected commands (test / lint / typecheck / build).
    python3 harmonist/agents/scripts/run_regression.py

    # Only a subset.
    python3 harmonist/agents/scripts/run_regression.py --steps test,lint

    # Different project root.
    python3 harmonist/agents/scripts/run_regression.py --project /path

    # JSON output for dashboards / tooling.
    python3 harmonist/agents/scripts/run_regression.py --json

    # Do not write the result to hooks state (dry / scripting).
    python3 harmonist/agents/scripts/run_regression.py --no-write

Exit codes:
    0  -- every requested step passed
    1  -- at least one step failed (details in output)
    2  -- detector / setup failure (no commands found, no project)
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
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent


def _import_detector():
    """detect_regression_commands.py is a sibling script; import it by
    path so this runner works whether invoked from the pack checkout
    or from an integrated project (via
    `harmonist/agents/scripts/`)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "detect_regression_commands",
        HERE / "detect_regression_commands.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _state_path(project: Path) -> Path | None:
    """Resolve the hooks state file so a passing run can be advertised
    to `gate-stop.sh`. Env override first, then project default."""
    env = os.environ.get("AGENT_PACK_HOOKS_STATE")
    if env:
        return Path(env)
    p = project / ".cursor" / "hooks" / ".state" / "session.json"
    return p if p.exists() or p.parent.exists() else None


def _run_step(name: str, cmd: str, cwd: Path, timeout: int,
              retries: int = 0) -> dict:
    """Run a single step via /bin/sh -c to honour shell quoting, capture
    stdout+stderr separately, return a structured record.

    If `retries > 0`, a non-zero exit code is retried up to `retries`
    additional times with small exponential backoff. The final record
    reports the total attempts and a flag `flaky` when the step
    eventually passed but only after >= 1 retry. This exists because
    network-dependent, docker-dependent, or clock-dependent tests can
    fail for infrastructure reasons and should not block the gate when
    a re-run would succeed."""
    started = time.time()
    attempts = 0
    last_proc: subprocess.CompletedProcess | None = None
    timeout_hit = False
    max_attempts = max(1, retries + 1)

    while attempts < max_attempts:
        attempts += 1
        try:
            last_proc = subprocess.run(
                cmd, shell=True, cwd=str(cwd),
                capture_output=True, text=True, timeout=timeout,
            )
            if last_proc.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            timeout_hit = True
            break  # timeouts aren't flakiness; surface them
        if attempts < max_attempts:
            time.sleep(min(2 ** (attempts - 1), 8))

    if timeout_hit:
        return {
            "step":     name,
            "command":  cmd,
            "exit_code": 124,
            "duration": timeout,
            "attempts": attempts,
            "flaky":    False,
            "stdout_tail": "",
            "stderr_tail": f"TIMEOUT after {timeout}s",
            "ran_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    assert last_proc is not None
    stdout_tail = "\n".join(last_proc.stdout.splitlines()[-20:])
    stderr_tail = "\n".join(last_proc.stderr.splitlines()[-20:])
    flaky = attempts > 1 and last_proc.returncode == 0
    return {
        "step":     name,
        "command":  cmd,
        "exit_code": last_proc.returncode,
        "duration": round(time.time() - started, 2),
        "attempts": attempts,
        "flaky":    flaky,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "ran_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _write_state(project: Path, results: list[dict], ok: bool) -> str | None:
    """Append the regression result to hooks state under
    `regression_results` (list of runs, latest last). Also bumps a
    telemetry counter for flaky-but-eventually-passed steps, so the
    usage report can surface infrastructure flakiness separately from
    real bugs."""
    sp = _state_path(project)
    if sp is None or not sp.parent.exists():
        return None
    try:
        data = json.loads(sp.read_text()) if sp.exists() else {}
    except Exception:
        data = {}
    runs = data.setdefault("regression_results", [])
    runs.append({
        "at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok":    ok,
        "steps": results,
    })
    # Keep only the last 10 runs to bound state size.
    if len(runs) > 10:
        del runs[0:len(runs) - 10]
    # Also set a convenience flag: last-regression-ok. gate-stop reads
    # only this when `require_regression_passed` is enabled.
    data["last_regression_ok"] = ok
    data["last_regression_at"] = runs[-1]["at"]
    try:
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(sp.parent))
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.close()
        os.replace(tmp.name, sp)
    except Exception as e:
        return f"could not write state: {e}"

    # Telemetry for flaky steps. Writes to .cursor/telemetry/agent-usage.json
    # if it exists (opt-in via the usual `telemetry_enabled` config).
    try:
        flaky_count = sum(1 for r in results if r.get("flaky"))
        if flaky_count > 0:
            tel = project / ".cursor" / "telemetry" / "agent-usage.json"
            if tel.parent.exists():
                tel_data = (json.loads(tel.read_text())
                            if tel.exists() else {})
                tel_data.setdefault("summaries", {})
                tel_data["summaries"]["regression_flaky_steps"] = (
                    int(tel_data["summaries"].get("regression_flaky_steps", 0))
                    + flaky_count
                )
                tel_data["last_update_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                import tempfile as _t
                tmp = _t.NamedTemporaryFile("w", delete=False, dir=str(tel.parent))
                json.dump(tel_data, tmp, indent=2, sort_keys=True)
                tmp.close()
                os.replace(tmp.name, tel)
    except Exception:
        pass  # telemetry failure is never fatal

    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--package", type=Path, default=None,
                    help="Monorepo: scope the run to a single package "
                         "(directory that carries its own manifests). "
                         "Detection + CWD both honour this path.")
    ap.add_argument("--steps", default="test,lint,typecheck,build",
                    help="Comma-separated step names to run "
                         "(from: test, lint, typecheck, build). "
                         "Defaults to all four.")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Per-step timeout in seconds (default: 600).")
    ap.add_argument("--retry", type=int, default=0,
                    help="On non-zero exit, retry a step up to N times with "
                         "exponential backoff. Timeouts are never retried "
                         "(they surface as a hard failure). A step that "
                         "eventually passes is marked `flaky: true` in the "
                         "result so the report_usage dashboard can track "
                         "infrastructure flakiness separately from real bugs.")
    ap.add_argument("--no-write", action="store_true",
                    help="Don't record the result in hooks state.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"run_regression: {project} is not a directory", file=sys.stderr)
        return 2

    # Monorepo: a `--package` narrows detection + cwd to that subtree
    # while the hooks state is still keyed on the root project.
    scan_root = args.package.resolve() if args.package else project
    run_cwd = scan_root
    if not scan_root.is_dir():
        print(f"run_regression: --package {scan_root} is not a directory",
              file=sys.stderr)
        return 2

    detector = _import_detector()
    detected = detector.detect_all(scan_root)
    # detect_all returns {step: [cmd, ...]}; we take the first entry per step.
    flat: dict[str, str] = {}
    for step, cmds in (detected or {}).items():
        if cmds:
            flat[step] = cmds[0]
    if not flat:
        print("run_regression: no commands detected in project manifests; "
              "nothing to run", file=sys.stderr)
        return 2

    wanted = {s.strip() for s in args.steps.split(",") if s.strip()}
    ordered = ["test", "lint", "typecheck", "build"]
    plan = [s for s in ordered if s in wanted and flat.get(s)]
    if not plan:
        print(f"run_regression: no commands to run for steps={sorted(wanted)} "
              f"(detected: {sorted(k for k, v in flat.items() if v)})",
              file=sys.stderr)
        return 2

    results: list[dict] = []
    overall_ok = True
    for step in plan:
        cmd = flat[step]
        if not args.json:
            print(f"==> {step}: {cmd}")
        res = _run_step(step, cmd, run_cwd, args.timeout, retries=args.retry)
        results.append(res)
        if res["exit_code"] != 0:
            overall_ok = False
            if not args.json:
                attempt_note = (f" after {res['attempts']} attempts"
                                if res["attempts"] > 1 else "")
                print(f"    FAIL (exit {res['exit_code']}, "
                      f"{res['duration']}s{attempt_note})")
                if res["stderr_tail"]:
                    print("    stderr tail:")
                    for ln in res["stderr_tail"].splitlines()[-8:]:
                        print(f"      {ln}")
        elif not args.json:
            flaky_note = f" (FLAKY: passed on attempt {res['attempts']})" \
                if res["flaky"] else ""
            print(f"    ok ({res['duration']}s){flaky_note}")

    write_err: str | None = None
    if not args.no_write:
        write_err = _write_state(project, results, overall_ok)

    if args.json:
        print(json.dumps({
            "project":    str(project),
            "plan":       plan,
            "ok":         overall_ok,
            "results":    results,
            "state_write_error": write_err,
        }, indent=2))
    else:
        if write_err:
            print(f"  WARN could not update hooks state: {write_err}")
        print(f"\n  Summary: {'PASSED' if overall_ok else 'FAILED'}  "
              f"({len(plan)} steps, "
              f"{sum(1 for r in results if r['exit_code']==0)} ok, "
              f"{sum(1 for r in results if r['exit_code']!=0)} failed)")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
