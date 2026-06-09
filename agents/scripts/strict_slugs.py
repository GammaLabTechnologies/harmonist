#!/usr/bin/env python3
"""
strict_slugs.py -- single source of truth for the pack-owned strict agent
slug sets, derived from agents/index.json (by protocol / category).

Six installer / verifier scripts used to carry hand-copied slug lists that
silently diverged (five variants existed; wcag-a11y-gate was missing from
several, so the gate advertised in AGENTS.md was never installed/verified).
They now all import the sets below. Import is by sibling module: every
consumer lives in agents/scripts/ and is invoked standalone
(`python3 path/to/script.py`), which puts this directory on sys.path.
None of the consumers is ever copied into a project's .cursor/ tree, so
the sibling import is always available; if the index itself is missing
(half-copied pack), a built-in fallback snapshot keeps the sets sane.

Exported subsets (all frozensets of slugs):

  ALL_STRICT            Every strict-protocol agent: the orchestration
                        category (repo-scout, agents-orchestrator) plus the
                        whole review category. These are pack-owned --
                        install_extras refuses to install them manually and
                        deintegrate removes every installed copy.
  REVIEWERS             category == "review" only (the mandatory gates).
  INSTALLED_BY_UPGRADE  What `upgrade.py --apply` copies into
                        .cursor/agents/: repo-scout + all reviewers EXCEPT
                        the background regression runner(s), which are
                        seeded separately by detect_regression_commands so
                        project-customised commands are never overwritten.
                        agents-orchestrator is also excluded: the project's
                        own AGENTS.md plays the orchestrator role.
  VERIFIED              What verify_integration / onboard expect to find
                        installed: INSTALLED_BY_UPGRADE + the seeded
                        background runner(s).

INSTALLED_BY_UPGRADE_RELPATHS gives the same set as pack-relative paths
("orchestration/repo-scout.md", ...) for copy-plan construction.
"""

from __future__ import annotations

import json
from pathlib import Path

# The orchestration scout that upgrade.py installs alongside the review
# gates. (agents-orchestrator, the other strict orchestration agent, is
# intentionally NOT installed -- see module docstring.)
SCOUT_SLUG = "repo-scout"

# Fallback snapshot used only when agents/index.json cannot be read.
# KEEP IN SYNC with the index -- the derivation below is authoritative
# whenever the index is present.
#   (slug, category, is_background)
_FALLBACK_ROWS = [
    ("agents-orchestrator", "orchestration", False),
    ("repo-scout", "orchestration", False),
    ("bg-regression-runner", "review", True),
    ("code-quality-auditor", "review", False),
    ("qa-verifier", "review", False),
    ("security-reviewer", "review", False),
    ("sre-observability", "review", False),
    ("wcag-a11y-gate", "review", False),
]


def _load_rows() -> list[tuple[str, str, bool]]:
    """Return (slug, category, is_background) for every strict /
    orchestration / review agent in the catalog index."""
    index_path = Path(__file__).resolve().parent.parent / "index.json"
    try:
        agents = json.loads(index_path.read_text(encoding="utf-8"))["agents"]
        rows = [
            (str(a["slug"]), str(a["category"]), bool(a.get("is_background")))
            for a in agents
            # The union keeps the sets stable even if a checkout predates
            # the agents-orchestrator persona->strict flip in the index.
            if a.get("protocol") == "strict"
            or a.get("category") in ("orchestration", "review")
        ]
        return rows or _FALLBACK_ROWS
    except Exception:
        return _FALLBACK_ROWS


_ROWS = _load_rows()

ALL_STRICT = frozenset(slug for slug, _cat, _bg in _ROWS)
REVIEWERS = frozenset(slug for slug, cat, _bg in _ROWS if cat == "review")

# Background reviewers (bg-regression-runner) are seeded per-project with
# detected test/lint/build commands; upgrade must never overwrite them.
_BACKGROUND_REVIEWERS = frozenset(
    slug for slug, cat, bg in _ROWS if cat == "review" and bg
)

INSTALLED_BY_UPGRADE = frozenset({SCOUT_SLUG}) | (REVIEWERS - _BACKGROUND_REVIEWERS)
VERIFIED = INSTALLED_BY_UPGRADE | _BACKGROUND_REVIEWERS

# Pack-relative agent paths for the copy plan, orchestration first.
INSTALLED_BY_UPGRADE_RELPATHS = [
    f"{cat}/{slug}.md"
    for slug, cat, _bg in sorted(_ROWS, key=lambda r: (r[1] != "orchestration", r[0]))
    if slug in INSTALLED_BY_UPGRADE
]


if __name__ == "__main__":
    # Tiny debugging aid: print the derived sets.
    for label, value in [
        ("ALL_STRICT", sorted(ALL_STRICT)),
        ("REVIEWERS", sorted(REVIEWERS)),
        ("INSTALLED_BY_UPGRADE", sorted(INSTALLED_BY_UPGRADE)),
        ("VERIFIED", sorted(VERIFIED)),
        ("INSTALLED_BY_UPGRADE_RELPATHS", INSTALLED_BY_UPGRADE_RELPATHS),
    ]:
        print(f"{label}: {value}")
