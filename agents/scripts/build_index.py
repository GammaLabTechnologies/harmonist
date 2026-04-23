#!/usr/bin/env python3
"""
build_index.py — emit agents/index.json from every agent file under agents/.

The index is the orchestrator's routing table. Rather than hard-coding agent
names in AGENTS.md, the orchestrator loads index.json and queries by tag,
category, or description match.

Output shape (stable, sorted, deterministic):
{
  "version": "2",
  "generated_from": "agents/<category>/<slug>.md",
  "schema_url": "agents/SCHEMA.md",
  "counts": { "total": N, "by_category": {...}, "by_protocol": {...} },
  "agents": [
    {
      "slug": "<filename stem>",
      "name": "<display name>",
      "description": "...",
      "category": "engineering",
      "protocol": "persona",
      "readonly": false,
      "is_background": false,
      "model": "inherit",
      "tags": [...],
      "domains": [...],
      "path": "agents/<category>/.../<slug>.md"
    },
    ...
  ],
  "by_category": { "engineering": ["slug1", "slug2", ...], ... },
  "by_tag":      { "security": [...], "review": [...], ... }
}

Deterministic: same input → same output byte-for-byte.
Safe: read-only (does not mutate any .md file).
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

import json
import sys
from pathlib import Path

# Re-use the parser from the migration script so both tools stay aligned.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from migrate_schema import parse_frontmatter, NON_AGENT_DIRS, REPO_AGENTS  # noqa: E402


INDEX_PATH = REPO_AGENTS / "index.json"


def load_agent(md: Path) -> dict | None:
    raw = md.read_text()
    if not raw.startswith("---\n"):
        return None
    end = raw.find("\n---\n", 4)
    if end == -1:
        print(f"ERROR {md}: no closing frontmatter", file=sys.stderr)
        return None
    fm_raw = raw[4:end]
    fields, _ = parse_frontmatter(fm_raw)

    # Required fields — fail loudly rather than silently ship a broken index.
    missing = [k for k in ("name", "description", "category", "protocol", "tags") if k not in fields]
    if missing:
        print(f"ERROR {md}: missing required fields: {missing}", file=sys.stderr)
        return None

    tags = fields.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.strip("[]").split(",") if t.strip()]
    domains = fields.get("domains") or ["all"]
    if isinstance(domains, str):
        domains = [d.strip() for d in domains.strip("[]").split(",") if d.strip()]

    # distinguishes_from / disambiguation (optional).
    dfrom = fields.get("distinguishes_from") or []
    if isinstance(dfrom, str):
        dfrom = [x.strip() for x in dfrom.strip("[]").split(",") if x.strip()]
    dfrom = sorted({str(p) for p in dfrom if p})
    disambig = str(fields.get("disambiguation") or "").strip()

    # Optional freshness / versioning metadata (W9).
    version = str(fields.get("version") or "").strip()
    updated_at = str(fields.get("updated_at") or "").strip()
    raw_deprecated = fields.get("deprecated")
    if isinstance(raw_deprecated, bool):
        deprecated: bool | str = raw_deprecated
    elif isinstance(raw_deprecated, str) and raw_deprecated.strip():
        deprecated = raw_deprecated.strip()
    else:
        deprecated = False

    return {
        "slug": md.stem,
        "name": str(fields["name"]),
        "description": str(fields["description"]),
        "category": str(fields["category"]),
        "protocol": str(fields["protocol"]),
        "readonly": bool(fields.get("readonly", False)),
        "is_background": bool(fields.get("is_background", False)),
        "model": str(fields.get("model", "inherit")),
        "tags": sorted(set(str(t) for t in tags)),
        "domains": sorted(set(str(d) for d in domains)),
        "distinguishes_from": dfrom,
        "disambiguation": disambig,
        "version": version,
        "updated_at": updated_at,
        "deprecated": deprecated,
        "path": str(md.relative_to(REPO_AGENTS.parent)),
    }


def build() -> dict:
    agents: list[dict] = []
    slugs_seen: dict[str, Path] = {}

    for cat_dir in sorted(REPO_AGENTS.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in NON_AGENT_DIRS or cat_dir.name.startswith("."):
            continue
        for md in sorted(cat_dir.rglob("*.md")):
            data = load_agent(md)
            if data is None:
                continue
            if data["slug"] in slugs_seen:
                print(
                    f"ERROR duplicate slug '{data['slug']}' at {md} and {slugs_seen[data['slug']]}",
                    file=sys.stderr,
                )
                continue
            slugs_seen[data["slug"]] = md
            agents.append(data)

    agents.sort(key=lambda a: (a["category"], a["slug"]))

    by_category: dict[str, list[str]] = {}
    by_protocol: dict[str, int] = {}
    by_tag: dict[str, list[str]] = {}
    by_domain: dict[str, list[str]] = {}
    # Disambiguation index: slug -> {"peers": [...], "note": "..."}
    # AND the reverse direction: any agent listing X as a peer gets X
    # added to its own peers bucket, so orchestrator lookups are symmetric.
    disambiguation: dict[str, dict] = {}

    for a in agents:
        by_category.setdefault(a["category"], []).append(a["slug"])
        by_protocol[a["protocol"]] = by_protocol.get(a["protocol"], 0) + 1
        for t in a["tags"]:
            by_tag.setdefault(t, []).append(a["slug"])
        for d in a.get("domains", []) or ["all"]:
            by_domain.setdefault(d, []).append(a["slug"])
        if a.get("distinguishes_from") or a.get("disambiguation"):
            disambiguation.setdefault(a["slug"], {"peers": [], "note": ""})
            disambiguation[a["slug"]]["peers"] = list(a.get("distinguishes_from") or [])
            disambiguation[a["slug"]]["note"] = a.get("disambiguation", "")

    # Symmetric edges: if A lists B in distinguishes_from, B's entry in
    # the disambiguation index also shows A as a peer.
    for slug, entry in list(disambiguation.items()):
        for peer in entry["peers"]:
            disambiguation.setdefault(peer, {"peers": [], "note": ""})
            if slug not in disambiguation[peer]["peers"]:
                disambiguation[peer]["peers"].append(slug)

    for lst in by_category.values():
        lst.sort()
    for lst in by_tag.values():
        lst.sort()
    for lst in by_domain.values():
        lst.sort()
    for entry in disambiguation.values():
        entry["peers"] = sorted(set(entry["peers"]))

    with_version = sum(1 for a in agents if a.get("version"))
    with_updated_at = sum(1 for a in agents if a.get("updated_at"))
    deprecated_count = sum(
        1 for a in agents
        if (isinstance(a.get("deprecated"), bool) and a.get("deprecated"))
        or (isinstance(a.get("deprecated"), str) and a.get("deprecated"))
    )

    return {
        "version": "2",
        "schema": "agents/SCHEMA.md",
        "counts": {
            "total": len(agents),
            "by_category": {k: len(v) for k, v in sorted(by_category.items())},
            "by_protocol": dict(sorted(by_protocol.items())),
            "by_domain": {k: len(v) for k, v in sorted(by_domain.items())},
            "with_disambiguation": len(disambiguation),
            "with_version": with_version,
            "with_updated_at": with_updated_at,
            "deprecated": deprecated_count,
        },
        "agents": agents,
        "by_category": dict(sorted(by_category.items())),
        "by_tag": dict(sorted(by_tag.items())),
        "by_domain": dict(sorted(by_domain.items())),
        "disambiguation": dict(sorted(disambiguation.items())),
    }


def main(argv: list[str]) -> int:
    index = build()
    payload = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=False) + "\n"

    if "--check" in argv:
        if not INDEX_PATH.exists():
            print(f"ERROR {INDEX_PATH} does not exist. Run build_index.py.", file=sys.stderr)
            return 1
        existing = INDEX_PATH.read_text()
        if existing != payload:
            print(
                f"ERROR {INDEX_PATH} is out of date. Run scripts/build_index.py and commit.",
                file=sys.stderr,
            )
            return 1
        print("index.json is up to date.")
        return 0

    INDEX_PATH.write_text(payload)
    print(f"Wrote {INDEX_PATH.relative_to(REPO_AGENTS.parent)} — {index['counts']['total']} agents.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
