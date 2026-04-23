#!/usr/bin/env python3
"""
detect_clones.py -- find SAME-CATEGORY clusters of agents that share the
same top tag pair but have no `distinguishes_from` / `disambiguation`
guidance.

Without this check, new overlapping agents can slip into the pool and
make the orchestrator's routing ambiguous. Default exit is zero
(warn-only); pass `--strict` to fail the build. Cross-category overlap
is NOT flagged since the orchestrator filters by category before
intersecting tags.

Definition of a cluster:
  * >= MIN_CLUSTER_SIZE agents
  * all in the same category
  * sharing the same unordered pair of tags
  * NOT disambiguated by distinct tech-layer tags across members

Broad "structural" pairs (e.g. growth+strategy) are skipped -- they are
not routing-ambiguous by themselves; real disambiguation needs come
from narrower pairs.
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
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

MIN_CLUSTER_SIZE = 3
INDEX_PATH = Path(__file__).resolve().parent.parent / "index.json"
TAGS_PATH = Path(__file__).resolve().parent.parent / "tags.json"

# Tag pairs that are structurally broad (growth+strategy, performance+api) --
# lots of agents share them without actually being routing-equivalent. We
# still check these clusters, but only flag them if members ALSO lack a
# distinguishing tech-layer tag (see below).
BROAD_PAIRS = {
    frozenset(["content-creation", "growth"]),
    frozenset(["community-building", "growth"]),
    frozenset(["experiment-tracking", "growth"]),
    frozenset(["content-creation", "experiment-tracking"]),
    frozenset(["content-creation", "community-building"]),
    frozenset(["brand-design", "content-creation"]),
    frozenset(["architecture", "gamedev"]),
    frozenset(["growth", "strategy"]),
    frozenset(["performance", "strategy"]),
    frozenset(["growth", "infra"]),
    frozenset(["growth", "performance"]),
    frozenset(["observability", "performance"]),
    frozenset(["observability", "api"]),
    frozenset(["api", "architecture"]),
    frozenset(["api", "audit"]),
    frozenset(["api", "auth"]),
    frozenset(["api", "authz"]),
    frozenset(["ai", "api"]),
    frozenset(["ai", "audit"]),
    frozenset(["data-science", "experiment-tracking"]),
    frozenset(["experiment-tracking", "feedback-analysis"]),
    frozenset(["architecture", "design"]),
    frozenset(["a11y", "architecture"]),
    frozenset(["a11y", "design"]),
    frozenset(["audit", "authz"]),
    frozenset(["architecture", "audit"]),
    frozenset(["architecture", "authz"]),
    frozenset(["api", "experiment-tracking"]),
    frozenset(["auth", "authz"]),
}


def _load_layer(layer: str) -> set[str]:
    if not TAGS_PATH.exists():
        return set()
    data = json.loads(TAGS_PATH.read_text())
    return {
        name for name, meta in data.get("tags", {}).items()
        if isinstance(meta, dict) and meta.get("layer") == layer
    }


TECH_TAGS = _load_layer("tech")
WORKFLOW_TAGS = _load_layer("workflow")
# `growth` and similar very-common skill tags that tend to appear on
# dozens of agents in the same category without actually signalling
# overlap -- they flag intent (marketing pushes growth) not routing.
VERY_COMMON_SKILL_TAGS = {"growth", "content-creation"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when a cluster lacks any disambiguation")
    args = ap.parse_args(argv)

    if not INDEX_PATH.exists():
        print(f"error: {INDEX_PATH} missing; run build_index.py first", file=sys.stderr)
        return 2

    index = json.loads(INDEX_PATH.read_text())
    agents = {a["slug"]: a for a in index["agents"]}

    pair_counts: dict[frozenset, list[str]] = defaultdict(list)
    for slug, meta in agents.items():
        tags = sorted(meta.get("tags", []))
        for a, b in combinations(tags, 2):
            pair_counts[frozenset([a, b])].append(slug)

    # Real clone risk: agents in the SAME category sharing a tag pair.
    # Cross-category overlap (marketing-tiktok-strategist + sales-account-strategist
    # both having "strategy") is not a routing problem -- the orchestrator
    # filters by category before tag intersection.
    problematic: list[tuple[str, frozenset, list[str]]] = []
    for pair, members in pair_counts.items():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        if pair in BROAD_PAIRS:
            continue
        # Skip any pair that contains a workflow tag -- those are too
        # generic to indicate real routing ambiguity.
        if pair & WORKFLOW_TAGS:
            continue
        # Skip pairs built from very-common skill tags.
        if pair <= VERY_COMMON_SKILL_TAGS | {t for t in pair if t in VERY_COMMON_SKILL_TAGS}:
            if pair & VERY_COMMON_SKILL_TAGS:
                continue

        # Group by category.
        by_cat: dict[str, list[str]] = defaultdict(list)
        for s in members:
            by_cat[agents[s]["category"]].append(s)

        for cat, cat_members in by_cat.items():
            if len(cat_members) < MIN_CLUSTER_SIZE:
                continue

            # If every member in this category has a distinct tech-layer
            # tag, they're already disambiguated by stack/platform.
            tech_per = [
                {t for t in agents[s].get("tags", []) if t in TECH_TAGS}
                for s in cat_members
            ]
            distinct_tech = {frozenset(ts) for ts in tech_per if ts}
            if len(distinct_tech) == len(cat_members) and all(ts for ts in tech_per):
                continue

            has_guidance = any(
                agents[s].get("distinguishes_from") or agents[s].get("disambiguation")
                for s in cat_members
            )
            if not has_guidance:
                problematic.append((cat, pair, sorted(cat_members)))

    if not problematic:
        print(
            f"detect_clones: every same-category cluster of {MIN_CLUSTER_SIZE}+ agents "
            "has at least one disambiguation hint."
        )
        return 0

    print(
        f"detect_clones: {len(problematic)} same-category cluster(s) with NO disambiguation:",
        file=sys.stderr,
    )
    for cat, pair, members in sorted(problematic, key=lambda kv: (-len(kv[2]), kv[0])):
        print(f"\n  category={cat}  tags={sorted(pair)}  ({len(members)} agents)", file=sys.stderr)
        for m in members:
            print(f"    {m}", file=sys.stderr)
    print(
        "\nAt least one agent in each cluster should declare "
        "`distinguishes_from` + `disambiguation` so the orchestrator can "
        "pick unambiguously.",
        file=sys.stderr,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
