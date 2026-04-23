#!/usr/bin/env python3
"""
integrate.py -- one-command integration of harmonist into a
project. Automates the deterministic half of the 11-step integration
prompt so only the judgment calls are left for the human / LLM.

What this script does (mechanically):

  1. Pack health preflight           -> check_pack_health.py
  2. Write pack-owned files          -> upgrade.py --apply
                                         (hooks, strict agents, memory
                                          tooling, schema, README, and
                                          the canonical protocol-enforce-
                                          ment rule)
  3. Auto-detect regression commands -> detect_regression_commands.py
                                         (seeds bg-regression-runner)
  4. Bootstrap AGENTS.md             -> if missing, copies the pack
                                         template into project root so
                                         the marker-blocks are present
                                         for future upgrades.
  5. Bootstrap project-domain-rules  -> copies .template into
                                         .cursor/rules/ if missing
  6. Bootstrap session-handoff entry -> memory.py append with a
                                         "integration complete" state
                                         record (correlation_id is
                                         consistent)
  7. Run the smoke test              -> smoke_test.py
  8. Run the verifier                -> verify_integration.py
  9. Print the manual follow-ups     -> things the script CAN'T do:
                                         customising Invariants, writing
                                         5-10 real domain rules, picking
                                         specialists beyond strict

What this script will NEVER do:

  - Pick specialists for you (requires domain knowledge).
  - Write Invariants / Platform Stack / Modules in AGENTS.md.
  - Write project-domain-rules.mdc content.
  - Make architecture decisions.

Usage:
    python3 harmonist/agents/scripts/integrate.py
    python3 harmonist/agents/scripts/integrate.py --project /path
    python3 harmonist/agents/scripts/integrate.py --dry-run
    python3 harmonist/agents/scripts/integrate.py --skip-smoke
    python3 harmonist/agents/scripts/integrate.py --json

Exit codes:
    0  -- integrated cleanly (or dry-run succeeded)
    1  -- integration incomplete (missing pieces the human needs to fix)
    2  -- preflight / setup failure
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
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


HERE = Path(__file__).resolve().parent
PACK_ROOT = HERE.parent.parent


@dataclass
class Step:
    name: str
    status: str = "pending"   # pending | ok | skipped | fail
    message: str = ""
    fix: str = ""
    detail: dict = field(default_factory=dict)


def _py(script: str, *args: str, cwd: Path | None = None,
        capture: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(script), *args]
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=capture, text=True,
        env={**os.environ, **(env or {})},
    )


def step_1_preflight(pack: Path) -> Step:
    s = Step("preflight")
    script = pack / "agents" / "scripts" / "check_pack_health.py"
    if not script.exists():
        s.status = "fail"
        s.message = "check_pack_health.py missing from pack"
        s.fix = "Re-clone the pack; something is truncated."
        return s
    r = _py(script, "--skip-slow", "--json", cwd=pack, capture=True)
    if r.returncode == 0:
        s.status = "ok"
        try:
            data = json.loads(r.stdout)
            summary = data.get("summary", {})
            s.message = (f"{summary.get('passed', '?')}/"
                         f"{summary.get('total', '?')} pack-health checks passed")
            s.detail = summary
        except Exception:
            s.message = "pack healthy"
        return s
    s.status = "fail"
    s.message = "pack health check failed; refusing to integrate"
    s.fix = f"Run manually for details: {script} (from pack root)"
    s.detail = {"stderr": r.stderr[-400:] if r.stderr else ""}
    return s


def step_2_upgrade(pack: Path, project: Path, apply: bool) -> Step:
    s = Step("upgrade-apply")
    script = pack / "agents" / "scripts" / "upgrade.py"
    args = ["--project", str(project), "--pack", str(pack)]
    if apply:
        args.append("--apply")
    r = _py(script, *args, capture=True)
    if r.returncode in (0, 1):  # 0 = clean dry-run, 1 = changes applied
        s.status = "ok"
        # Summarise last line of stdout
        tail = (r.stdout.splitlines() or ["(no output)"])[-1]
        s.message = tail.strip()
        return s
    s.status = "fail"
    s.message = f"upgrade.py exited {r.returncode}"
    s.fix = f"Run manually: python3 {script} --project {project} --pack {pack} --apply"
    s.detail = {"stderr": r.stderr[-400:] if r.stderr else "",
                "stdout_tail": r.stdout[-400:] if r.stdout else ""}
    return s


def step_3_bg_regression(pack: Path, project: Path, apply: bool) -> Step:
    """upgrade.py --apply already seeds bg-regression-runner when commands
    are detected. This step merely confirms detection worked."""
    s = Step("bg-regression-runner-commands")
    script = pack / "agents" / "scripts" / "detect_regression_commands.py"
    r = _py(script, "--project", str(project), "--json", capture=True)
    try:
        data = json.loads(r.stdout or "{}")
        detected = {k: v for k, v in data.get("commands", {}).items() if v}
    except Exception:
        detected = {}

    if not detected:
        s.status = "skipped"
        s.message = ("no project manifests detected; bg-regression-runner.md "
                     "will keep the placeholder until you fill in real commands")
        return s

    installed = project / ".cursor" / "agents" / "bg-regression-runner.md"
    if apply and not installed.exists():
        s.status = "fail"
        s.message = "commands detected but bg-regression-runner.md was not installed"
        s.fix = ("Re-run: python3 harmonist/agents/scripts/"
                 "upgrade.py --project . --apply")
        return s

    s.status = "ok"
    s.message = (f"detected commands for steps: "
                 f"{sorted(detected)} (bg-regression-runner.md seeded)")
    return s


def step_4_agents_md(pack: Path, project: Path, apply: bool) -> Step:
    s = Step("agents-md-bootstrap")
    target = project / "AGENTS.md"
    if target.exists():
        # Already there (user wrote it OR upgrade.py-merged it). We treat
        # this as a "human hand-off" signal: the file exists but the
        # caller MAY need to customise Invariants / Platform Stack etc.
        s.status = "ok"
        s.message = "AGENTS.md present (customise Invariants + Platform Stack + Modules)"
        return s
    src = pack / "AGENTS.md"
    if not src.exists():
        s.status = "fail"
        s.message = "pack's AGENTS.md template missing"
        return s
    if apply:
        shutil.copy2(src, target)
        s.status = "ok"
        s.message = (f"copied pack AGENTS.md into project root "
                     "(NOW: customise Invariants + Platform Stack + Modules)")
    else:
        s.status = "skipped"
        s.message = "would copy pack AGENTS.md into project root (dry-run)"
    return s


def step_5_rules(pack: Path, project: Path, apply: bool) -> Step:
    s = Step("cursor-rules")
    rules_dir = project / ".cursor" / "rules"
    protocol = rules_dir / "protocol-enforcement.mdc"
    domain = rules_dir / "project-domain-rules.mdc"

    domain_template = pack / "agents" / "templates" / "rules" / "project-domain-rules.mdc.template"

    missing_pieces: list[str] = []
    if not protocol.exists():
        missing_pieces.append("protocol-enforcement.mdc (was `upgrade.py --apply` run?)")
    if not domain.exists():
        if apply and domain_template.exists():
            rules_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(domain_template, domain)
        else:
            missing_pieces.append("project-domain-rules.mdc (5-10 domain-specific rules go here)")

    if missing_pieces:
        s.status = "fail"
        s.message = "; ".join(missing_pieces)
        s.fix = ("Re-run upgrade.py --apply; OR copy the pack template "
                 f"from {domain_template}")
        return s
    s.status = "ok"
    s.message = "protocol-enforcement.mdc + project-domain-rules.mdc present"
    return s


def step_6_memory_bootstrap(pack: Path, project: Path, apply: bool) -> Step:
    s = Step("memory-bootstrap")
    memory_cli = project / ".cursor" / "memory" / "memory.py"
    if not memory_cli.exists():
        s.status = "fail"
        s.message = "memory.py missing from project (upgrade.py should have installed it)"
        s.fix = "Re-run upgrade.py --apply"
        return s

    # Seed template markdown files if missing. upgrade.py intentionally
    # never touches these (they accrue project history), so integrate.py
    # bootstraps them on first install only.
    memory_dir = project / ".cursor" / "memory"
    seeded: list[str] = []
    for name in ("session-handoff.md", "decisions.md", "patterns.md"):
        tgt = memory_dir / name
        src = pack / "memory" / name
        if not tgt.exists() and src.exists():
            if apply:
                shutil.copy2(src, tgt)
            seeded.append(name)

    handoff = memory_dir / "session-handoff.md"

    # If session-handoff already carries a "Integration complete" bootstrap
    # entry, we've run before; don't duplicate.
    already_bootstrapped = False
    if handoff.exists():
        txt = handoff.read_text(errors="replace")
        if "Integration complete; project state = bootstrap" in txt:
            already_bootstrapped = True

    if already_bootstrapped:
        s.status = "ok"
        s.message = ("session-handoff already has the integration-complete "
                     "bootstrap entry; skipping")
        return s

    if not apply:
        s.status = "skipped"
        s.message = "would append an 'integration complete' state entry (dry-run)"
        return s

    # Seed one state entry so subsequent sessions don't start from
    # template-only memory.
    env = {
        "AGENT_PACK_HOOKS_STATE": str(
            project / ".cursor" / "hooks" / ".state" / "session.json"
        ),
    }
    r = _py(
        memory_cli, "append",
        "--file", "session-handoff",
        "--kind", "state",
        "--status", "in_progress",
        "--summary", "Integration complete; project state = bootstrap",
        "--tags", "bootstrap,integration",
        "--body", (
            "## Current State\n"
            "- harmonist installed via integrate.py\n"
            "- Pack-owned files refreshed, hooks active, rules installed.\n\n"
            "## Next Steps for the team\n"
            "- Customise AGENTS.md with real Invariants / Platform Stack / Modules.\n"
            "- Fill project-domain-rules.mdc with 5-10 concrete domain rules.\n"
            "- Pick 3-10 specialist agents from the pack catalog and install into "
            ".cursor/agents/ (see README for the selection procedure)."
        ),
        cwd=project, capture=True, env=env,
    )
    if r.returncode == 0:
        s.status = "ok"
        s.message = "seeded 'integration complete' state entry in session-handoff.md"
        return s
    s.status = "fail"
    s.message = f"memory.py append failed (exit {r.returncode})"
    s.detail = {"stderr": r.stderr[-400:] if r.stderr else ""}
    return s


def step_7_smoke(pack: Path, project: Path, apply: bool, skip: bool) -> Step:
    s = Step("smoke-test")
    if skip or not apply:
        s.status = "skipped"
        s.message = "smoke test skipped (dry-run or --skip-smoke)"
        return s
    script = pack / "agents" / "scripts" / "smoke_test.py"
    r = _py(script, "--project", str(project), capture=True)
    tail = "\n".join((r.stderr or r.stdout).splitlines()[-8:])
    if r.returncode == 0:
        s.status = "ok"
        s.message = "happy + negative paths pass; enforcement is live"
        return s
    s.status = "fail"
    s.message = f"smoke_test.py exited {r.returncode}"
    s.fix = (f"Inspect: python3 {script} --project {project}. "
             "Likely causes: hooks not executable, memory not bootstrapped.")
    s.detail = {"tail": tail}
    return s


def step_8_verify(pack: Path, project: Path, apply: bool) -> Step:
    s = Step("verify-integration")
    if not apply:
        s.status = "skipped"
        s.message = "verifier skipped (dry-run)"
        return s
    script = pack / "agents" / "scripts" / "verify_integration.py"
    r = _py(script, "--project", str(project), "--json", capture=True)
    try:
        data = json.loads(r.stdout or "{}")
        summary = data.get("summary", {})
        err = int(summary.get("error", 0))
        warn = int(summary.get("warning", 0))
    except Exception:
        err = 1 if r.returncode else 0
        warn = 0
    if r.returncode == 0 and err == 0:
        s.status = "ok"
        s.message = f"integration objectively verified (warnings: {warn})"
        return s
    s.status = "fail"
    s.message = f"verifier found {err} error(s), {warn} warning(s)"
    s.fix = (f"Inspect: python3 {script} --project {project}. "
             "Each error ships with a one-line FIX hint.")
    return s


def render_plan(steps: list[Step], apply: bool) -> str:
    icon = {"ok": "✓", "fail": "✖", "skipped": "~", "pending": "·"}
    lines: list[str] = []
    for s in steps:
        lines.append(f"  {icon.get(s.status, '?')}  {s.name}: {s.message}")
        if s.status == "fail" and s.fix:
            for f in s.fix.splitlines():
                lines.append(f"       FIX: {f}")
    return "\n".join(lines)


def next_steps_summary() -> str:
    return (
        "\n"
        "Next steps for the team / orchestrator (the script CAN'T do these):\n"
        "\n"
        "  1. Edit AGENTS.md (in project root):\n"
        "     - Replace 'YOUR PROJECT' placeholder at the top with your\n"
        "       project's domain identity (one paragraph).\n"
        "     - Fill 'Platform Stack', 'Modules', 'Invariants',\n"
        "       'Resilience' sections between the pack-owned markers.\n"
        "     - Preserve `<!-- pack-owned:... -->` marker pairs; upgrade.py\n"
        "       refreshes only those blocks.\n"
        "\n"
        "  2. Edit .cursor/rules/project-domain-rules.mdc:\n"
        "     - Replace examples with 5-10 CONCRETE rules specific to\n"
        "       YOUR domain (the kind where violation == real bug).\n"
        "\n"
        "  3. Pick specialists from the catalogue:\n"
        "     - Intersect project tags with agents/index.json.\n"
        "     - Copy 3-10 .md files into .cursor/agents/.\n"
        "     - Strict reviewers are already installed; don't add more\n"
        "       generic reviewers.\n"
        "\n"
        "  4. Start a NEW chat in Cursor so the rules re-apply. Orchestrator\n"
        "     reads session-handoff.md first, routes via index.json.\n"
        "\n"
        "Anything that went wrong above has a FIX line. Re-run this script\n"
        "after fixing; it is idempotent.\n"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--pack", type=Path, default=PACK_ROOT,
                    help="Pack root (directory with VERSION + agents/).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write anything; just show the plan.")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="Skip the smoke test (faster re-runs during tuning).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    pack = args.pack.resolve()
    apply = not args.dry_run
    if not project.is_dir():
        print(f"integrate: --project {project} is not a directory", file=sys.stderr)
        return 2
    if not (pack / "VERSION").exists():
        print(f"integrate: --pack {pack} is not a valid pack (no VERSION file)", file=sys.stderr)
        return 2

    steps: list[Step] = []
    steps.append(step_1_preflight(pack))
    if steps[-1].status == "fail":
        # Preflight blocks everything.
        print(render_plan(steps, apply))
        print("\nIntegration aborted at preflight.")
        return 2

    # AGENTS.md must exist BEFORE upgrade.py --apply (upgrade refuses
    # without it). The bootstrap step is cheap and safe to run first.
    steps.append(step_4_agents_md(pack, project, apply))
    steps.append(step_2_upgrade(pack, project, apply))
    steps.append(step_3_bg_regression(pack, project, apply))
    steps.append(step_5_rules(pack, project, apply))
    steps.append(step_6_memory_bootstrap(pack, project, apply))
    steps.append(step_7_smoke(pack, project, apply, args.skip_smoke))
    steps.append(step_8_verify(pack, project, apply))

    if args.json:
        payload = {
            "project": str(project),
            "pack": str(pack),
            "dry_run": args.dry_run,
            "steps": [asdict(s) for s in steps],
            "ok": not any(s.status == "fail" for s in steps),
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("Integration plan:" if args.dry_run else "Integration:")
        print("")
        print(render_plan(steps, apply))
        print(next_steps_summary())

    return 0 if all(s.status in ("ok", "skipped") for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
