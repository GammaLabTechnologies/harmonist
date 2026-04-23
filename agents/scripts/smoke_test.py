#!/usr/bin/env python3
"""
smoke_test.py -- end-to-end exercise of the enforcement pipeline in an
integrated project. Runs the real hook scripts with synthetic inputs so
we know the installation actually fires, not just that the config files
exist.

Two tests, both rooted in the project's .cursor/:

  1. HAPPY PATH
     * sessionStart hook -> state file created
     * afterFileEdit with a sentinel path -> state.writes records it
     * subagentStart + subagentStop with 'AGENT: qa-verifier' ->
       state.reviewers_seen contains qa-verifier
     * memory.py append of a real state entry -> session-handoff gains
       an entry with the active correlation_id
     * stop hook -> allows; task_seq advanced

  2. NEGATIVE PATH (gate bites)
     * same as above minus the reviewer delegation
     * stop hook must return 'followup_message'

Both are pure tooling -- no LLM required. If any step fails the script
points at the exact reason + how to fix.

Usage:
    python3 harmonist/agents/scripts/smoke_test.py
    python3 harmonist/agents/scripts/smoke_test.py --project /path
    python3 harmonist/agents/scripts/smoke_test.py --json

Exit codes:
    0 = all tests passed
    1 = one or more tests failed
    2 = cannot run (hooks / memory not installed -- run upgrade.py first)
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
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Step:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Scenario:
    name: str
    steps: list[Step] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps)


def _run_hook(project: Path, script: str, stdin_json: str) -> subprocess.CompletedProcess:
    path = project / ".cursor" / "hooks" / "scripts" / script
    if not path.exists():
        raise FileNotFoundError(path)
    return subprocess.run(
        ["bash", str(path)], input=stdin_json,
        capture_output=True, text=True, cwd=str(project), check=False,
    )


def _load_state(project: Path) -> dict:
    p = project / ".cursor" / "hooks" / ".state" / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _reset_state(project: Path) -> None:
    state_dir = project / ".cursor" / "hooks" / ".state"
    if state_dir.exists():
        for f in state_dir.iterdir():
            try:
                f.unlink()
            except Exception:
                pass


def _preflight(project: Path) -> tuple[bool, list[str]]:
    """Return (ok, missing). Bail early if the install is incomplete."""
    required = [
        project / ".cursor" / "hooks" / "scripts" / "seed-session.sh",
        project / ".cursor" / "hooks" / "scripts" / "record-write.sh",
        project / ".cursor" / "hooks" / "scripts" / "record-subagent-start.sh",
        project / ".cursor" / "hooks" / "scripts" / "record-subagent-stop.sh",
        project / ".cursor" / "hooks" / "scripts" / "gate-stop.sh",
        project / ".cursor" / "memory" / "memory.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    return (not missing, missing)


def scenario_happy(project: Path) -> Scenario:
    s = Scenario("happy-path")
    _reset_state(project)

    # 1. sessionStart -> state created
    _run_hook(project, "seed-session.sh", "{}")
    state = _load_state(project)
    s.steps.append(Step(
        "sessionStart creates state", bool(state.get("session_id")),
        "state.session_id present" if state.get("session_id") else "no session.json after seed",
    ))

    # 2. afterFileEdit on a sentinel path in /tmp (not in skip_path_patterns)
    sentinel = "/tmp/asp-smoke-sentinel.py"
    _run_hook(project, "record-write.sh", json.dumps({"file_path": sentinel}))
    state = _load_state(project)
    writes = state.get("writes") or []
    s.steps.append(Step(
        "afterFileEdit records the sentinel write",
        any(w.get("path") == sentinel for w in writes),
        f"state.writes now {writes}",
    ))

    # 3. subagentStart + subagentStop with AGENT: qa-verifier marker
    _run_hook(project, "record-subagent-start.sh",
              json.dumps({
                  "subagent_type": "generalPurpose",
                  "prompt": "AGENT: qa-verifier\nverify sentinel write",
              }))
    _run_hook(project, "record-subagent-stop.sh",
              json.dumps({"subagent_type": "generalPurpose"}))
    state = _load_state(project)
    seen = state.get("reviewers_seen") or []
    s.steps.append(Step(
        "subagent markers credit qa-verifier",
        "qa-verifier" in seen,
        f"state.reviewers_seen = {seen}",
    ))

    # 4. memory.py append a state entry with the active correlation_id.
    # --allow-duplicate because smoke_test re-runs intentionally hit the
    # same summary; the dedupe guard would block the test otherwise.
    memory_cli = project / ".cursor" / "memory" / "memory.py"
    rc = subprocess.run(
        ["python3", str(memory_cli), "append",
         "--file", "session-handoff", "--kind", "state", "--status", "done",
         "--summary", "smoke-test: hooks pipeline fires end-to-end",
         "--allow-duplicate",
         "--body", "Automated smoke test verified the full protocol path. "
                   "Sentinel write, qa-verifier review, and this handoff entry "
                   "landed through the real hook scripts."],
        capture_output=True, text=True, cwd=str(project), check=False,
    )
    s.steps.append(Step(
        "memory.py append succeeds",
        rc.returncode == 0,
        (rc.stdout.strip() if rc.returncode == 0 else rc.stderr.strip())[:200],
    ))

    # Mimic the implicit record-write for memory: the real hook would fire
    # on this file too. Inject it so the stop gate sees it.
    handoff_path = str(project / ".cursor" / "memory" / "session-handoff.md")
    _run_hook(project, "record-write.sh", json.dumps({"file_path": handoff_path}))

    # 5. stop hook -> must allow and bump task_seq
    state_before = _load_state(project)
    prev_seq = int(state_before.get("task_seq", 0))
    out = _run_hook(project, "gate-stop.sh", "{}")
    state_after = _load_state(project)
    try:
        response = json.loads(out.stdout or "{}")
    except Exception:
        response = {}
    followed = "followup_message" in response
    s.steps.append(Step(
        "stop gate allows after full protocol",
        not followed,
        f"stop response keys={list(response.keys())}; stderr head: {out.stderr[:160]}",
    ))
    s.steps.append(Step(
        "task_seq advanced after successful stop",
        int(state_after.get("task_seq", 0)) == prev_seq + 1,
        f"task_seq {prev_seq} -> {state_after.get('task_seq')}",
    ))

    return s


def scenario_negative(project: Path) -> Scenario:
    s = Scenario("negative-path-gate-bites")
    _reset_state(project)
    _run_hook(project, "seed-session.sh", "{}")
    sentinel = "/tmp/asp-smoke-sentinel-2.py"
    _run_hook(project, "record-write.sh", json.dumps({"file_path": sentinel}))
    # deliberately skip reviewer + memory
    out = _run_hook(project, "gate-stop.sh", "{}")
    try:
        response = json.loads(out.stdout or "{}")
    except Exception:
        response = {}
    s.steps.append(Step(
        "stop gate bites when reviewer + handoff missing",
        "followup_message" in response,
        f"response keys={list(response.keys())}",
    ))
    # Clean up state so the happy scenario runs fresh next time.
    _reset_state(project)
    return s


def render(scenarios: list[Scenario], quiet: bool) -> str:
    lines: list[str] = []
    total = failed = 0
    for sc in scenarios:
        lines.append(f"\n  {sc.name}:")
        for st in sc.steps:
            total += 1
            if not st.passed:
                failed += 1
            icon = "ok  " if st.passed else "FAIL"
            lines.append(f"    {icon}  {st.name}")
            if st.detail and (not st.passed or not quiet):
                lines.append(f"           {st.detail[:260]}")
    lines.append("")
    lines.append(f"  Summary: {total - failed}/{total} steps passed across "
                 f"{len(scenarios)} scenario(s), {failed} failure(s).")
    lines.append("  OK" if failed == 0 else "  FAILED -- see per-step details above.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not (project / "AGENTS.md").exists():
        print(f"smoke_test: no AGENTS.md at {project}. Integrate first.", file=sys.stderr)
        return 2
    ok, missing = _preflight(project)
    if not ok:
        print("smoke_test: installation incomplete. Missing:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("\nRun: python3 harmonist/agents/scripts/upgrade.py --apply",
              file=sys.stderr)
        return 2

    scenarios = [scenario_happy(project), scenario_negative(project)]
    failed = sum(1 for sc in scenarios for st in sc.steps if not st.passed)

    if args.json:
        print(json.dumps({
            "project": str(project),
            "scenarios": [
                {
                    "name": sc.name,
                    "passed": sc.passed,
                    "steps": [asdict(st) for st in sc.steps],
                }
                for sc in scenarios
            ],
            "summary": {
                "total_steps": sum(len(sc.steps) for sc in scenarios),
                "failed_steps": failed,
            },
        }, indent=2))
    else:
        print(render(scenarios, args.quiet))
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
