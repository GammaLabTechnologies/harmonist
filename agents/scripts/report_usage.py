#!/usr/bin/env python3
"""
report_usage.py -- render the local telemetry collected by the
enforcement hooks so you can see which agents actually earn their
slot in .cursor/agents/ and which are dead balance.

All data is local to the project (.cursor/telemetry/agent-usage.json).
Nothing is shipped anywhere.

Usage:
    python3 harmonist/agents/scripts/report_usage.py
    python3 harmonist/agents/scripts/report_usage.py --recommend-removal
    python3 harmonist/agents/scripts/report_usage.py --json
    python3 harmonist/agents/scripts/report_usage.py --project /path

Exit codes:
    0 = printed a report
    1 = telemetry is empty (nothing to report yet)
    2 = project root / telemetry file not found
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
import sys
from pathlib import Path


INSTALLED_DIR = Path(".cursor") / "agents"
TELEMETRY_FILE = Path(".cursor") / "telemetry" / "agent-usage.json"


def _load(project: Path) -> dict:
    p = project / TELEMETRY_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _installed_slugs(project: Path) -> set[str]:
    base = project / INSTALLED_DIR
    if not base.exists():
        return set()
    return {p.stem for p in base.rglob("*.md")}


def _iso_to_age_hours(iso: str) -> float | None:
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None
    delta = dt.datetime.now(dt.timezone.utc) - t
    return delta.total_seconds() / 3600


def _human_age(hours: float | None) -> str:
    if hours is None:
        return "(unknown)"
    if hours < 1:
        return f"{int(hours * 60)}m ago"
    if hours < 24:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.0f}d ago"


def render_report(project: Path, data: dict, installed: set[str],
                  recommend_removal: bool) -> str:
    lines: list[str] = []
    summaries = data.get("summaries") or {}
    agents = data.get("agents") or {}

    lines.append(f"  telemetry file: {project / TELEMETRY_FILE}")
    lines.append(f"  first entry:    {data.get('started_at', '(unknown)')}")
    lines.append(f"  last update:    {data.get('last_update_at', '(unknown)')}")
    lines.append("")

    # Protocol health
    lines.append("  Protocol health")
    lines.append("  ---------------")
    sessions = summaries.get("sessions", 0)
    lines.append(f"    sessions seen                    {sessions}")
    lines.append(f"    allows (protocol satisfied)      {summaries.get('gate_allow_satisfied', 0)}")
    lines.append(f"    allows (no writes, pure Q&A)     {summaries.get('gate_allow_no_writes', 0)}")
    lines.append(f"    PROTOCOL-SKIP opt-outs           {summaries.get('protocol_skips', 0)}")
    lines.append(f"    followup_message returned        {summaries.get('gate_followups', 0)}")
    if sessions:
        ratio = summaries.get("gate_followups", 0) / max(sessions, 1)
        tag = "healthy" if ratio < 0.5 else "noisy -- check agent behaviour"
        lines.append(f"    gate-bite rate                   {ratio:.2f}  ({tag})")
    lines.append("")

    # Usage ranking
    ranked = sorted(
        agents.items(),
        key=lambda kv: (-int(kv[1].get("invocations", 0)), kv[0]),
    )
    lines.append("  Top agents by invocation")
    lines.append("  ------------------------")
    if not ranked:
        lines.append("    (no agent invocations recorded yet)")
    else:
        for slug, meta in ranked[:15]:
            inv = int(meta.get("invocations", 0))
            last = _human_age(_iso_to_age_hours(meta.get("last_at", "")))
            lines.append(f"    {inv:>5}  {slug:<42s}  last {last}")
    lines.append("")

    # Dead balance: installed but never invoked
    invoked = set(agents.keys())
    dead = sorted(installed - invoked)
    if dead:
        lines.append(f"  Installed but NEVER invoked ({len(dead)} agents)")
        lines.append("  ------------------------------------------")
        for slug in dead[:30]:
            lines.append(f"    - {slug}")
        if len(dead) > 30:
            lines.append(f"    ... and {len(dead) - 30} more")
        lines.append("")
        if recommend_removal:
            lines.append("  Recommendation")
            lines.append("  --------------")
            lines.append(
                "    These agents occupy context but have zero recorded use.\n"
                "    Consider moving them out of .cursor/agents/ (keep them in\n"
                "    the pack catalog). Re-install later if a task needs them."
            )
            lines.append("")
            lines.append("    Suggested command:")
            for slug in dead:
                if slug in {"repo-scout", "security-reviewer", "code-quality-auditor",
                            "qa-verifier", "sre-observability", "bg-regression-runner"}:
                    continue  # strict agents: never recommend removal
                lines.append(f"      rm '.cursor/agents/{slug}.md'")

    # Invoked but not installed -- typically happens if you removed files
    # without clearing telemetry, or if somebody invoked an agent by raw
    # prompt copy-paste.
    orphan = sorted(invoked - installed)
    if orphan:
        lines.append(f"  Invoked but NOT installed ({len(orphan)} agents)")
        lines.append("  -------------------------------------")
        for slug in orphan[:20]:
            inv = int(agents[slug].get("invocations", 0))
            lines.append(f"    {inv:>5}  {slug}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--recommend-removal", action="store_true",
                    help="Print a `rm` plan for agents installed but never invoked "
                         "(never includes the strict review+orchestration set).")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"report_usage: project {project} does not exist", file=sys.stderr)
        return 2

    data = _load(project)
    if not data:
        msg = (f"report_usage: no telemetry at {project / TELEMETRY_FILE}. "
               "Run a Cursor session (or smoke_test.py) with hooks installed first.")
        if args.json:
            print(json.dumps({"project": str(project), "empty": True, "message": msg}, indent=2))
            return 1
        print(msg, file=sys.stderr)
        return 1

    installed = _installed_slugs(project)

    if args.json:
        # Enrich with installed / orphan / dead lists.
        invoked = set((data.get("agents") or {}).keys())
        payload = {
            "project": str(project),
            **data,
            "installed": sorted(installed),
            "dead_balance": sorted(installed - invoked),
            "invoked_not_installed": sorted(invoked - installed),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(render_report(project, data, installed, args.recommend_removal))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
