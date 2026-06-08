#!/usr/bin/env python3
"""
validate.py -- validate the hardening checklist against schema.json.

Pure standard library (no PyYAML / jsonschema dependency, in keeping with the
pack's zero-dependency rule). Checks the structural rules the schema declares:
each section has the required keys, slugs are unique and well-formed, every
checklist item has point/priority/details, and priority is in the enum read
from schema.json. Also lists / aggregates by priority so the checklist stays
honest as it grows.

Usage:
    python3 playbooks/checklists/validate.py [--file <checklist.json>] [--quiet]

Exit codes: 0 = valid, 1 = problems found, 2 = cannot run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE / "security-privacy-hardening.json"
SCHEMA_FILE = HERE / "schema.json"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _priority_enum() -> set:
    try:
        schema = json.loads(SCHEMA_FILE.read_text())
        return set(
            schema["items"]["properties"]["checklist"]["items"]
            ["properties"]["priority"]["enum"]
        )
    except Exception:
        return {"essential", "recommended", "advanced"}


def validate(path: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    counts = {"sections": 0, "items": 0}
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return [f"{path.name}: not valid JSON: {e}"], counts
    if not isinstance(data, list):
        return [f"{path.name}: top level must be an array of sections"], counts

    priorities = _priority_enum()
    seen_slugs: set = set()
    for i, sec in enumerate(data):
        where = f"section[{i}]"
        if not isinstance(sec, dict):
            problems.append(f"{where}: must be an object")
            continue
        for key in ("title", "slug", "intro", "checklist"):
            if key not in sec:
                problems.append(f"{where}: missing required key '{key}'")
        slug = sec.get("slug", "")
        if slug:
            where = f"section '{slug}'"
            if not SLUG_RE.match(slug):
                problems.append(f"{where}: slug must match [a-z0-9][a-z0-9-]*")
            if slug in seen_slugs:
                problems.append(f"{where}: duplicate slug")
            seen_slugs.add(slug)
        items = sec.get("checklist")
        if not isinstance(items, list) or not items:
            problems.append(f"{where}: checklist must be a non-empty array")
            continue
        counts["sections"] += 1
        for j, it in enumerate(items):
            counts["items"] += 1
            if not isinstance(it, dict):
                problems.append(f"{where} item[{j}]: must be an object")
                continue
            for key in ("point", "priority", "details"):
                if key not in it or not str(it.get(key, "")).strip():
                    problems.append(f"{where} item[{j}]: missing/empty '{key}'")
            pr = it.get("priority")
            if pr is not None and pr not in priorities:
                problems.append(
                    f"{where} item[{j}]: priority '{pr}' not in {sorted(priorities)}")
            counts[f"priority:{pr}"] = counts.get(f"priority:{pr}", 0) + 1
    return problems, counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.file.exists():
        print(f"validate: {args.file} not found", file=sys.stderr)
        return 2
    problems, counts = validate(args.file)
    for p in problems:
        print(f"ERROR {p}")
    if not args.quiet:
        pri = {k.split(":", 1)[1]: v for k, v in counts.items() if k.startswith("priority:")}
        print(f"\nchecklist: {counts['sections']} section(s), {counts['items']} item(s); "
              f"by priority: {pri}")
    if problems:
        print("FAILED")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
