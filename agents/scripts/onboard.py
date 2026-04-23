#!/usr/bin/env python3
"""
onboard.py -- "what is all this?" for teammates who cloned a repo that
ALREADY has harmonist integrated. They didn't run the original
11-step integration and they probably don't know what .cursor/hooks/
or memory.py is. This script gives them the 90-second tour.

Three modes:

  default      Human-friendly walkthrough: what's installed, what each
               piece does, how to work with it, what's expected of them.
  --check      Read-only health probe: is everything wired up on this
               machine? Prints fix commands for anything off.
  --json       Same information in machine form, for dashboards.

The script reads:
  - AGENTS.md           -> project identity, invariants, domain
  - .cursor/pack-version.json -> which pack version was integrated
  - .cursor/agents/*.md -> installed specialists
  - .cursor/hooks.json  -> enforcement hooks
  - .cursor/memory/*.md -> current shared memory
  - .cursor/rules/*.mdc -> Cursor rules

Nothing is mutated. Exit codes:
  0  healthy
  1  some pieces missing / misconfigured (details in output)
  2  this folder doesn't look integrated at all
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
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    message: str
    fix: str = ""


@dataclass
class Snapshot:
    project_root: str
    integrated: bool = False
    pack_version: str = ""
    domain_identity: str = ""
    invariants: list[str] = field(default_factory=list)
    platform_stack: list[str] = field(default_factory=list)
    installed_agents: list[str] = field(default_factory=list)
    strict_agents: list[str] = field(default_factory=list)
    hooks_count: int = 0
    rules_count: int = 0
    memory_entries: int = 0
    checks: list[Check] = field(default_factory=list)


STRICT_SLUGS = {
    "qa-verifier",
    "security-reviewer",
    "code-quality-auditor",
    "sre-observability",
    "bg-regression-runner",
    "repo-scout",
    "wcag-a11y-gate",
}


def _read_agents_md(project: Path) -> tuple[str, list[str], list[str]]:
    """Return (domain_identity, invariants, platform_stack) extracted
    from project's AGENTS.md. Best-effort parsing: looks for the
    standard pack sections."""
    p = project / "AGENTS.md"
    if not p.exists():
        return ("", [], [])
    text = p.read_text(errors="replace")

    # Domain identity: first non-empty line after "# AGENTS" or the
    # first level-1 heading's following paragraph.
    identity = ""
    m = re.search(
        r"(?:^|\n)#\s+\S[^\n]*\n+(?:<!--[^\n]*\n+)*([^\n][^\n]{20,200})",
        text,
    )
    if m:
        identity = m.group(1).strip()

    def _section(title_rx: str, limit: int = 15) -> list[str]:
        rx = re.compile(
            r"(?:^|\n)#{2,4}\s+(?:" + title_rx + r")\s*\n(.+?)(?=\n#{2,4}\s|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        m = rx.search(text)
        if not m:
            return []
        block = m.group(1)
        items = re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$",
                           block, re.MULTILINE)
        cleaned = []
        for it in items:
            it = re.sub(r"\s+", " ", it).strip()
            if it and not it.startswith("<!--"):
                cleaned.append(it[:200])
            if len(cleaned) >= limit:
                break
        return cleaned

    invariants = _section(r"Invariants?")
    stack = _section(r"Platform\s+Stack|Stack|Tech(?:nology)?\s+Stack")
    return (identity, invariants, stack)


def _list_installed_agents(project: Path) -> list[str]:
    adir = project / ".cursor" / "agents"
    if not adir.exists():
        return []
    return sorted(p.stem for p in adir.rglob("*.md"))


def _count_memory_entries(project: Path) -> int:
    mdir = project / ".cursor" / "memory"
    if not mdir.exists():
        return 0
    count = 0
    for p in mdir.glob("*.md"):
        count += len(re.findall(r"<!--\s*memory-entry:start\s*-->",
                                p.read_text(errors="replace")))
    return count


def _read_pack_version(project: Path) -> str:
    p = project / ".cursor" / "pack-version.json"
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text()).get("pack_version", "")
    except Exception:
        return ""


def _count_hooks(project: Path) -> int:
    p = project / ".cursor" / "hooks.json"
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text())
        return sum(len(v) if isinstance(v, list) else 0
                   for v in data.values())
    except Exception:
        return 0


def _count_rules(project: Path) -> int:
    rdir = project / ".cursor" / "rules"
    if not rdir.exists():
        return 0
    return len(list(rdir.glob("*.mdc")))


def run_checks(project: Path) -> tuple[Snapshot, list[Check]]:
    snap = Snapshot(project_root=str(project))
    snap.integrated = (project / ".cursor").is_dir() and (project / "AGENTS.md").exists()
    snap.pack_version = _read_pack_version(project)

    identity, inv, stack = _read_agents_md(project)
    snap.domain_identity = identity
    snap.invariants = inv
    snap.platform_stack = stack

    snap.installed_agents = _list_installed_agents(project)
    snap.strict_agents = [s for s in snap.installed_agents if s in STRICT_SLUGS]
    snap.hooks_count = _count_hooks(project)
    snap.rules_count = _count_rules(project)
    snap.memory_entries = _count_memory_entries(project)

    checks: list[Check] = []
    if not snap.integrated:
        checks.append(Check(
            "integrated", False,
            "This project does not appear to have harmonist installed.",
            "If you expected it to be installed, check with the person who cloned the original integration."))
        return (snap, checks)

    checks.append(Check(
        "agents-md", bool(snap.domain_identity),
        "AGENTS.md carries a domain identity." if snap.domain_identity
        else "AGENTS.md exists but no domain identity was detected.",
        "" if snap.domain_identity
        else "Open AGENTS.md and read the top. If the first paragraph is still the template placeholder, ask your teammate to re-run integration step 3."))

    checks.append(Check(
        "invariants", len(snap.invariants) >= 3,
        f"{len(snap.invariants)} project invariants listed in AGENTS.md.",
        "" if len(snap.invariants) >= 3
        else "AGENTS.md should list at least 3 concrete invariants. Add them."))

    checks.append(Check(
        "strict-agents", len(snap.strict_agents) >= 5,
        f"{len(snap.strict_agents)} strict reviewer agents installed ({', '.join(snap.strict_agents[:4])}{'…' if len(snap.strict_agents)>4 else ''}).",
        "" if len(snap.strict_agents) >= 5
        else "Some strict reviewers are missing under `.cursor/agents/`. Run `python3 harmonist/agents/scripts/upgrade.py --apply` from the project root."))

    checks.append(Check(
        "hooks", snap.hooks_count >= 5,
        f"{snap.hooks_count} enforcement hooks registered.",
        "" if snap.hooks_count >= 5
        else "Cursor hooks are missing. Run `upgrade.py --apply`."))

    checks.append(Check(
        "rules", snap.rules_count >= 1,
        f"{snap.rules_count} Cursor rule(s) in `.cursor/rules/`.",
        "" if snap.rules_count >= 1
        else "No Cursor rules. Copy `harmonist/agents/templates/rules/protocol-enforcement.mdc` into `.cursor/rules/`."))

    checks.append(Check(
        "memory", (project / ".cursor" / "memory" / "session-handoff.md").exists(),
        f"{snap.memory_entries} memory entries present.",
        "" if (project / ".cursor" / "memory" / "session-handoff.md").exists()
        else "Memory directory missing. Run `upgrade.py --apply`."))

    checks.append(Check(
        "pack-version", bool(snap.pack_version),
        f"Pack version {snap.pack_version!r}." if snap.pack_version
        else "Pack version not recorded; upgrade path may be unreliable.",
        "" if snap.pack_version
        else "Run `upgrade.py --apply` to record the current pack version."))

    return (snap, checks)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

WALKTHROUGH = """\
Welcome aboard. This project uses **harmonist**: a protocol-
driven AI coding workflow with:

  1. **{domain_blurb}**
  2. A catalogue of AI specialist agents that an orchestrator routes
     to based on what you're asking. You don't pick agents by hand.
  3. Mechanical enforcement via Cursor hooks -- when you ask for
     code changes, a review agent has to approve before the turn
     ends. A stop-gate blocks responses that skipped required steps.
  4. Persistent memory (`.cursor/memory/`) that records decisions
     and carries context between sessions.

What's installed here
---------------------
{status_block}

What's expected of you
----------------------
- Read `AGENTS.md` in the repo root. It defines the project's
  identity, platform stack, and non-negotiable invariants.
- Read `.cursor/memory/session-handoff.md` BEFORE starting real
  work -- it has the current project state.
- When you open Cursor in this folder, it will pick up
  `.cursor/rules/protocol-enforcement.mdc` automatically; follow
  the protocol it sets.
- For trivial edits (docs, typos, README), the stop-gate now has
  a lightweight fast-path: edits confined to `.md/.txt/README/
  CHANGELOG/.gitignore/docs/` are auto-allowed without a reviewer.
  For anything code-shaped the full gate runs.

Things you can't do
-------------------
- Edit a strict agent file (e.g. `security-reviewer.md`) by hand.
  They're pack-owned; changes are overwritten by `upgrade.py`.
- Hand-write memory entries. Use `.cursor/memory/memory.py append`
  so correlation IDs stay consistent.
- Skip `AGENT:` markers on subagent prompts. Hooks rely on them.

Daily loop
----------
- Read session-handoff.md, state your goal, let the orchestrator
  plan. Respond to the protocol, not around it.
- When you finish a real task, the stop-gate will ensure a reviewer
  ran and memory got appended. You don't usually need to call these
  yourself -- the orchestrator routes them.

Need help?
----------
- Full architecture:  `harmonist/README.md`
- Agent index:        `harmonist/agents/index.json`
- Health probe:       `python3 harmonist/agents/scripts/check_pack_health.py`
- Usage report:       `python3 harmonist/agents/scripts/report_usage.py`
"""


def render_walkthrough(snap: Snapshot, checks: list[Check]) -> str:
    if not snap.integrated:
        return (
            "This folder does NOT look integrated with harmonist.\n"
            "  - expected `.cursor/` directory and `AGENTS.md` at the project root.\n"
            "If you cloned the repo fresh and expected a working setup,\n"
            "ask the teammate who integrated it to re-run\n"
            "  python3 harmonist/agents/scripts/upgrade.py --apply\n"
            "from the project root on their machine and push the resulting\n"
            ".cursor/ tree + AGENTS.md markers.\n"
        )

    domain = snap.domain_identity or "project-specific AGENTS.md"
    domain_blurb = (domain[:120] + "…") if len(domain) > 120 else domain

    lines: list[str] = []
    for c in checks:
        icon = "✓" if c.ok else "✖"
        lines.append(f"  {icon} {c.name}: {c.message}")
        if not c.ok and c.fix:
            for fl in c.fix.splitlines():
                lines.append(f"      FIX: {fl}")
    status_block = "\n".join(lines)

    # Top 8 installed specialists (non-strict), to give a taste.
    specialists = [s for s in snap.installed_agents
                   if s not in STRICT_SLUGS][:8]
    if specialists:
        status_block += "\n\n  Specialists the orchestrator can currently route to:"
        for s in specialists:
            status_block += f"\n    - {s}"
        if len(snap.installed_agents) - len(snap.strict_agents) > 8:
            status_block += (
                f"\n    + {len(snap.installed_agents) - len(snap.strict_agents) - 8} "
                "more (see `.cursor/agents/`)"
            )

    if snap.invariants:
        status_block += "\n\n  Project invariants (from AGENTS.md):"
        for inv in snap.invariants[:5]:
            status_block += f"\n    - {inv}"

    if snap.platform_stack:
        status_block += "\n\n  Platform stack (from AGENTS.md):"
        for p in snap.platform_stack[:6]:
            status_block += f"\n    - {p}"

    return WALKTHROUGH.format(
        domain_blurb=domain_blurb,
        status_block=status_block,
    )


def render_check(snap: Snapshot, checks: list[Check]) -> str:
    lines: list[str] = []
    if not snap.integrated:
        return "  ✖ not integrated"
    for c in checks:
        icon = "✓" if c.ok else "✖"
        lines.append(f"  {icon}  {c.name}: {c.message}")
        if not c.ok and c.fix:
            for fl in c.fix.splitlines():
                lines.append(f"       FIX: {fl}")
    fails = sum(1 for c in checks if not c.ok)
    oks = len(checks) - fails
    lines.append("")
    lines.append(f"  Summary: {oks}/{len(checks)} healthy, {fails} issue(s).")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--check", action="store_true",
                    help="Health probe only; exit non-zero on issues.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"onboard: {project} is not a directory", file=sys.stderr)
        return 2

    snap, checks = run_checks(project)

    if args.json:
        print(json.dumps({
            "snapshot": asdict(snap),
            "checks": [asdict(c) for c in checks],
        }, indent=2, sort_keys=True, default=str))
    elif args.check:
        print(render_check(snap, checks))
    else:
        print(render_walkthrough(snap, checks))

    if not snap.integrated:
        return 2
    if any(not c.ok for c in checks):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
