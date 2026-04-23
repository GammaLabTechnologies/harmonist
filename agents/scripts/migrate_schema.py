#!/usr/bin/env python3
"""
migrate_schema.py — bring every agent markdown under agents/ up to Schema v2.

Required frontmatter keys (see agents/SCHEMA.md):
  name, description, category, protocol, readonly, is_background, model, tags

Optional preserved keys (when present):
  domains, color, emoji, vibe, tools, author

Behaviour:
  * Parses existing frontmatter (flat, one-line values; lists as [a, b] or YAML
    list form).
  * Fills missing required fields using per-category defaults.
  * Derives tags from filename/description keywords + category.
  * Re-emits frontmatter in a canonical key order so running the script twice
    is a no-op.

Idempotent. Safe to run repeatedly.
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
import os
import re
import sys
from pathlib import Path

REPO_AGENTS = Path(__file__).resolve().parent.parent  # agents/
TAGS_JSON = REPO_AGENTS / "tags.json"

# ---------------------------------------------------------------------------
# Schema versioning
#
# Every agent file carries a `schema_version` frontmatter field. The linter
# rejects files that claim a version the tooling does not know about; the
# migrator walks a declared chain of upgrade functions so a file on vN can
# be brought forward to CURRENT_SCHEMA_VERSION without manual edits.
#
# Bump CURRENT_SCHEMA_VERSION when making a BREAKING change to the agent
# schema (renaming/removing a required field, changing an enum, tightening
# a validation rule). Add a (from, to, upgrade_fn) entry to MIGRATIONS
# so existing agents can roll forward. Never DELETE historical upgrade
# functions -- they are the only path for stale forks.
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION = "2"
KNOWN_SCHEMA_VERSIONS = {"1", "2"}  # everything the linter will accept as-is

# Optional agent-level freshness metadata. Stamped when an agent file is
# missing them entirely so `scan_agent_freshness.py` can track aging;
# once present they are left alone (idempotent).
#
# BASELINE_UPDATED_AT intentionally reflects the pack release that first
# introduced universal metadata coverage, NOT today's date -- that way
# running the migrator on a fresh clone produces a deterministic diff
# rather than a mutating "stamp everything with $(today)" churn.
BASELINE_VERSION = "1.0.0"
BASELINE_UPDATED_AT = "2026-04-23"

# ---------------------------------------------------------------------------
# Canonical key ordering — anything not listed drops to the bottom preserved
# in original order.
# ---------------------------------------------------------------------------
CANONICAL_ORDER = [
    "schema_version",
    "name",
    "description",
    "category",
    "protocol",
    "readonly",
    "is_background",
    "model",
    "tags",
    "domains",
    "distinguishes_from",
    "disambiguation",
    "version",
    "updated_at",
    "deprecated",
    "color",
    "emoji",
    "vibe",
    "tools",
    "author",
]

REQUIRED = {
    "schema_version",
    "name",
    "description",
    "category",
    "protocol",
    "readonly",
    "is_background",
    "model",
    "tags",
}

# Only these keys are allowed in the frontmatter. Anything else (e.g. an
# agent's ad-hoc `services:` block) is dropped during migration and the user
# is expected to move that content to the body if they want to keep it.
ALLOWED_KEYS = set(CANONICAL_ORDER)

BOOL_FIELDS = {"readonly", "is_background"}
LIST_FIELDS = {"tags", "domains"}

# Folders under agents/ that must NOT be treated as agent categories.
NON_AGENT_DIRS = {"scripts", "integrations", "templates", ".git"}

# Default protocol by category
STRICT_CATEGORIES = {"orchestration", "review"}

# Model tiers. The three values are the only legal entries in an agent's
# `model:` frontmatter field.
#   fast       -- cheap / small-context; used for mechanical tasks where
#                 depth is wasted (scout, regression runner, short-form
#                 marketing copy, generator prompts).
#   inherit    -- use whatever the host session is using. Safe default for
#                 write agents whose task size varies wildly.
#   reasoning  -- expensive / deep-thinking tier. Used when getting it
#                 wrong is costly: security audits, architecture design,
#                 review gates, compliance verdicts.
MODEL_TIERS = {"fast", "inherit", "reasoning"}

# ---------------------------------------------------------------------------
# Domain scoping (used to filter irrelevant agents per project)
# ---------------------------------------------------------------------------
#
# `all` agents are universally applicable. Everything else targets a
# specific project shape (regional market, vertical, runtime). The
# integration prompt asks for the project's domains and filters the
# catalog via `index.json.by_domain` so (e.g.) a TON Marketplace
# project doesn't see the WeChat Mini Program developer in its picks.
ALLOWED_DOMAINS = {
    "all",
    # Regional markets where content / distribution rules differ sharply.
    "china-market",
    "korean-market",
    "french-market",
    # Verticals with specialised agents in the pool.
    "fintech",
    "blockchain",
    "gamedev",
    "xr",
    "healthcare",
    "academic",
    "gov-tech",
    "education",
}

# Whole categories whose agents share a single obvious domain.
PER_CATEGORY_DOMAINS: dict[str, list[str]] = {
    "game-development":  ["gamedev"],
    "spatial-computing": ["xr"],
    "academic":          ["academic"],
}

# Hand-curated per-slug overrides. Any slug here wins over both
# PER_CATEGORY_DOMAINS and the default `[all]`.
FIXED_DOMAINS: dict[str, list[str]] = {
    # China-market specialists.
    "marketing-baidu-seo-specialist":                      ["china-market"],
    "marketing-bilibili-content-strategist":               ["china-market"],
    "marketing-china-ecommerce-operator":                  ["china-market"],
    "marketing-china-market-localization-strategist":      ["china-market"],
    "marketing-douyin-strategist":                         ["china-market"],
    "marketing-kuaishou-strategist":                       ["china-market"],
    "marketing-livestream-commerce-coach":                 ["china-market"],
    "marketing-private-domain-operator":                   ["china-market"],
    "marketing-short-video-editing-coach":                 ["china-market"],
    "marketing-wechat-official-account":                   ["china-market"],
    "marketing-weibo-strategist":                          ["china-market"],
    "marketing-xiaohongshu-specialist":                    ["china-market"],
    "marketing-zhihu-strategist":                          ["china-market"],
    "engineering-feishu-integration-developer":            ["china-market"],
    "engineering-wechat-mini-program-developer":           ["china-market"],
    "healthcare-marketing-compliance":                     ["china-market", "healthcare"],
    "government-digital-presales-consultant":              ["china-market", "gov-tech"],

    # Regional specialists.
    "specialized-korean-business-navigator":               ["korean-market"],
    "specialized-french-consulting-market":                ["french-market"],

    # Blockchain specialists. The Solidity engineer lives in engineering/
    # but only makes sense in a blockchain project.
    "engineering-solidity-smart-contract-engineer":        ["blockchain"],
    "blockchain-security-auditor":                         ["blockchain"],
    "zk-steward":                                          ["all"],  # knowledge-management, not chain-specific
    # Academic agents are categorised at academic, no extra override needed.

    # Education specialists.
    "study-abroad-advisor":                                ["education"],

    # Gov-tech
    "support-legal-compliance-checker":                    ["all"],  # general legal review
    "corporate-training-designer":                         ["all"],
}

# Category-level model defaults. Applied when the agent has no explicit
# override in FIXED_MODELS.
PER_CATEGORY_MODEL = {
    "orchestration":     "fast",
    "review":            "reasoning",
    "engineering":       "inherit",
    "design":            "inherit",
    "testing":           "inherit",
    "product":           "inherit",
    "project-management":"inherit",
    "marketing":         "fast",
    "paid-media":        "inherit",
    "sales":             "inherit",
    "finance":           "reasoning",   # analysis + numbers, worth the depth
    "support":           "inherit",
    "academic":          "reasoning",
    "game-development":  "inherit",
    "spatial-computing": "inherit",
    "specialized":       "inherit",
}

# Per-agent overrides. Reserved for the protocol-critical roles (strict
# agents, deep audits, pure content generators) where category-level
# defaults would get it wrong.
FIXED_MODELS: dict[str, str] = {
    # Orchestration
    "repo-scout":                                          "fast",
    # Review -- all reasoning EXCEPT the background command runner.
    "security-reviewer":                                   "reasoning",
    "code-quality-auditor":                                "reasoning",
    "qa-verifier":                                         "reasoning",
    "sre-observability":                                   "reasoning",
    "bg-regression-runner":                                "fast",
    # Engineering personas that do deep design / audit work.
    "engineering-security-engineer":                       "reasoning",
    "engineering-threat-detection-engineer":               "reasoning",
    "engineering-backend-architect":                       "reasoning",
    "engineering-software-architect":                      "reasoning",
    "engineering-database-optimizer":                      "reasoning",
    "engineering-autonomous-optimization-architect":       "reasoning",
    "engineering-solidity-smart-contract-engineer":        "reasoning",
    "engineering-ai-engineer":                             "reasoning",
    "engineering-ai-data-remediation-engineer":            "reasoning",
    "engineering-incident-response-commander":             "reasoning",
    # Engineering personas that are mostly mechanical / fast-iteration.
    "engineering-rapid-prototyper":                        "fast",
    "engineering-technical-writer":                        "fast",
    # Testing
    "testing-reality-checker":                             "reasoning",
    "testing-evidence-collector":                          "fast",
    "testing-workflow-optimizer":                          "inherit",
    # Specialized deep-audit roles
    "blockchain-security-auditor":                         "reasoning",
    "compliance-auditor":                                  "reasoning",
    "specialized-workflow-architect":                      "reasoning",
    "specialized-salesforce-architect":                    "reasoning",
    "agentic-identity-trust":                              "reasoning",
    "specialized-model-qa":                                "reasoning",
    "specialized-mcp-builder":                             "reasoning",
    "zk-steward":                                          "reasoning",
    # Specialized / mechanical
    "specialized-document-generator":                      "fast",
    "report-distribution-agent":                           "fast",
    "sales-data-extraction-agent":                         "fast",
    "data-consolidation-agent":                            "fast",
    "accounts-payable-agent":                              "reasoning",
    # Design prompt generators -- short, stateless, cheap
    "design-image-prompt-engineer":                        "fast",
    # Marketing: short-form copy is fast; strategic / analytic work is deeper.
    "marketing-growth-hacker":                             "reasoning",
    "marketing-ai-citation-strategist":                    "reasoning",
    "marketing-agentic-search-optimizer":                  "reasoning",
    "marketing-china-market-localization-strategist":      "reasoning",
    "marketing-book-co-author":                            "reasoning",
    # Product -- discovery / decisions are reasoning; sprint ops are not.
    "product-manager":                                     "reasoning",
    "product-trend-researcher":                            "reasoning",
    "product-sprint-prioritizer":                          "inherit",
    "product-feedback-synthesizer":                        "inherit",
    "product-behavioral-nudge-engine":                     "reasoning",
    # Sales analytical roles
    "sales-pipeline-analyst":                              "reasoning",
    "sales-deal-strategist":                               "reasoning",
}

# Full set of agent categories — used to filter cross-category "leaks" in tags.
# A tag that matches a category name is only allowed if it IS the agent's own
# category. Prevents e.g. the word "support" in a description from tagging an
# unrelated agent with the support category.
ALL_CATEGORIES = {
    "orchestration", "review", "engineering", "design", "testing", "product",
    "project-management", "marketing", "paid-media", "sales", "finance",
    "support", "academic", "game-development", "spatial-computing",
    "specialized",
}

# Generic role nouns that appear in dozens of agent names but carry no routing
# signal. Routing never benefits from "engineer" or "specialist" as a tag —
# users query for domain + skill, not job title.
ROLE_NOUN_BLACKLIST = {
    "engineer", "engineers",
    "specialist", "specialists",
    "architect", "architects",
    "strategist", "strategists",
    "manager", "managers",
    "coordinator", "coordinators",
    "auditor", "auditors",
    "researcher", "researchers",
    "developer", "developers",
    "analyst", "analysts",
    "lead", "leads",
    "senior", "junior",
    "expert", "experts",
}

def _load_vocab() -> tuple[dict, dict, dict]:
    """Return (allowed_tags, synonyms_map, per_category_defaults).

    allowed_tags: dict keyed by tag name -> layer.
    synonyms_map: lowercase phrase -> canonical tag name (includes the tag
                  itself as one of its own synonyms).
    per_category_defaults: category -> list[tag].
    """
    if not TAGS_JSON.exists():
        return {}, {}, {}
    try:
        data = json.loads(TAGS_JSON.read_text())
    except Exception as exc:
        print(f"ERROR loading {TAGS_JSON}: {exc}", file=sys.stderr)
        return {}, {}, {}
    allowed: dict[str, str] = {}
    synonyms: dict[str, str] = {}
    for name, meta in data.get("tags", {}).items():
        if name.startswith("_"):
            continue  # doc-only keys like "_layer_skill"
        layer = meta.get("layer", "") if isinstance(meta, dict) else ""
        allowed[name] = layer
        synonyms[name.lower()] = name
        for syn in (meta.get("synonyms") if isinstance(meta, dict) else []) or []:
            synonyms[str(syn).lower()] = name
    per_cat = data.get("per_category_defaults", {})
    return allowed, synonyms, per_cat


ALLOWED_TAGS, TAG_SYNONYMS, PER_CATEGORY_TAGS = _load_vocab()


# Backward-compat alias: existing imports from this module reference TAG_VOCAB.
# It now comes from tags.json. If the vocab file is missing, we degrade
# gracefully to an empty set (lint will then fail loudly with a clear error).
TAG_VOCAB = set(ALLOWED_TAGS.keys())

# ---------------------------------------------------------------------------
# Minimal frontmatter parser (flat, deterministic)
# ---------------------------------------------------------------------------


def parse_frontmatter(raw: str) -> tuple[dict, list[str]]:
    """Return (fields, order) where order preserves original field sequence."""
    fields: dict[str, object] = {}
    order: list[str] = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$", line)
        if not m:
            # Unexpected — keep as raw sentinel
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        # Block scalar or list
        if rest in ("", "|", ">"):
            # gather subsequent indented lines
            collected: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t") or lines[i].startswith("-")):
                collected.append(lines[i])
                i += 1
            if rest in ("|", ">"):
                fields[key] = "\n".join(line.strip() for line in collected)
            elif all(s.lstrip().startswith("-") for s in collected if s.strip()):
                fields[key] = [s.split("-", 1)[1].strip() for s in collected if s.strip()]
            else:
                fields[key] = "\n".join(collected).strip()
            if key not in order:
                order.append(key)
            continue
        # Inline list: [a, b, c]
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            fields[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        else:
            val = rest.strip().strip("'\"")
            if val.lower() == "true":
                fields[key] = True
            elif val.lower() == "false":
                fields[key] = False
            else:
                fields[key] = val
        if key not in order:
            order.append(key)
        i += 1
    return fields, order


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


def _emit_value(key: str, value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        # Use inline form when short enough
        rendered = ", ".join(str(v) for v in value)
        return f"[{rendered}]"
    v = str(value)
    # Quote if contains ":" at position that may confuse YAML or leading special char
    needs_quote = False
    if v.startswith(("{", "[", "&", "*", "!", "|", ">", "%", "@", "`", "#")):
        needs_quote = True
    if v.startswith("#"):
        needs_quote = True
    if needs_quote:
        # Prefer single quotes; escape any single quotes in value
        esc = v.replace("'", "''")
        return f"'{esc}'"
    return v


def emit_frontmatter(fields: dict, preserve_order: list[str]) -> str:
    """Emit frontmatter in canonical order; unknown keys append in original order."""
    out_lines: list[str] = []
    emitted: set[str] = set()
    for key in CANONICAL_ORDER:
        if key in fields:
            out_lines.append(f"{key}: {_emit_value(key, fields[key])}")
            emitted.add(key)
    for key in preserve_order:
        if key not in emitted and key in fields:
            out_lines.append(f"{key}: {_emit_value(key, fields[key])}")
            emitted.add(key)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Tag derivation
# ---------------------------------------------------------------------------


MAX_DERIVED_TAGS = 10

# Hand-curated overrides for agents whose tags must be exactly right: any
# entries here fully replace automatic derivation for that slug. Reserved
# for strict-protocol agents (orchestration + review) where routing is
# gate-critical.
FIXED_TAGS: dict[str, list[str]] = {
    # orchestration
    "repo-scout":            ["orchestration", "scout", "architecture"],
    # review (readonly reviewers that drive the protocol's mandatory gates)
    "security-reviewer":     ["review", "audit", "security", "owasp", "auth", "authz", "secrets"],
    "code-quality-auditor":  ["review", "audit", "refactoring", "minimal-change", "architecture"],
    "qa-verifier":           ["review", "qa", "evidence-collection", "regression", "reality-check"],
    "sre-observability":     ["review", "sre", "observability", "reliability", "performance", "scaling", "database-design", "query-optimization", "caching"],
    "bg-regression-runner":  ["review", "regression", "qa"],
}

# Weights for automatic derivation (how strong is the signal?). Existing
# curated tags always stay (up to the cap); these weights only rank the
# *derived* tags filling remaining slots.
_SCORE_CATEGORY_DEFAULT = 400
_SCORE_FILENAME = 300
_SCORE_DESCRIPTION = 50
_SCORE_BODY = 2
_SCORE_MULTIWORD_BONUS = 40


def _canonicalize(raw: str, category: str) -> str | None:
    """Return the canonical tag name or None if it fails vocabulary / policy."""
    t = str(raw).strip().lower()
    if not t:
        return None
    canonical = TAG_SYNONYMS.get(t, t)
    if canonical not in ALLOWED_TAGS:
        return None
    if canonical in ALL_CATEGORIES and canonical != category:
        return None
    if canonical in ROLE_NOUN_BLACKLIST:
        return None
    return canonical


def derive_tags(
    name: str,
    description: str,
    category: str,
    existing: list[str] | None,
    body: str = "",
) -> list[str]:
    """Compute tags from the curated vocabulary.

    Algorithm (idempotent):
      1. Seed the output with `existing` tags that survive vocabulary +
         policy filters, in order -- manual curation is preserved.
      2. Rank *derived* tags from (category, category defaults, filename,
         description, body) by weighted score and append them until the
         list reaches MAX_DERIVED_TAGS.
      3. The agent's own category is guaranteed to be present.

    Because step 1 is a plain filter (no "bonus"), running the migrator
    twice yields the same output. Running it once on a previously
    auto-tagged file also yields the same output, so first-and-second
    passes converge.
    """
    if not ALLOWED_TAGS:
        return [category] if category else []

    # Fixed overrides short-circuit the derivation entirely.
    if name in FIXED_TAGS:
        return [t for t in FIXED_TAGS[name] if t in ALLOWED_TAGS][:MAX_DERIVED_TAGS]

    chosen: list[str] = []
    seen: set[str] = set()

    def push(tag: str | None) -> None:
        if tag and tag not in seen and len(chosen) < MAX_DERIVED_TAGS:
            chosen.append(tag)
            seen.add(tag)

    # 1. Preserve existing curated tags (filter through vocab / policy).
    for t in existing or []:
        push(_canonicalize(t, category))

    # Ensure own category is in the output no matter what.
    push(_canonicalize(category, category))

    if len(chosen) >= MAX_DERIVED_TAGS:
        return chosen

    # 2. Derive additional tags by scoring.
    scores: dict[str, int] = {}

    def add(raw: str, weight: int) -> None:
        c = _canonicalize(raw, category)
        if c and c not in seen:
            scores[c] = scores.get(c, 0) + weight

    for t in PER_CATEGORY_TAGS.get(category, []):
        add(t, _SCORE_CATEGORY_DEFAULT)

    slug = name.lower()
    slug = re.sub(rf"^{re.escape(category)}-", "", slug)
    for p in (p for p in re.split(r"[-_]", slug) if p):
        add(p, _SCORE_FILENAME)

    desc_lower = (description or "").lower()
    body_lower = (body or "").lower()

    multi_word = sorted(
        (phrase for phrase in TAG_SYNONYMS if " " in phrase),
        key=lambda p: -len(p),
    )
    for phrase in multi_word:
        if phrase in desc_lower:
            add(phrase, _SCORE_DESCRIPTION + _SCORE_MULTIWORD_BONUS)
        elif phrase in body_lower:
            add(phrase, _SCORE_BODY + _SCORE_MULTIWORD_BONUS)

    for tok in re.findall(r"[a-z0-9][a-z0-9-]*", desc_lower):
        add(tok, _SCORE_DESCRIPTION)

    # Body counts. A single stray mention is noise (e.g. an example in a
    # persona agent's body); require either presence in the description OR
    # at least BODY_MIN_REPEATS occurrences for a body-only tag to count.
    BODY_MIN_REPEATS = 3
    desc_tokens = set(re.findall(r"[a-z0-9][a-z0-9-]*", desc_lower))
    desc_canonicals = {TAG_SYNONYMS.get(t, t) for t in desc_tokens}

    body_counts: dict[str, int] = {}
    for tok in re.findall(r"[a-z0-9][a-z0-9-]*", body_lower):
        canonical = TAG_SYNONYMS.get(tok, tok)
        if canonical in ALLOWED_TAGS:
            body_counts[canonical] = body_counts.get(canonical, 0) + 1
    for tag, count in body_counts.items():
        if tag in desc_canonicals or count >= BODY_MIN_REPEATS:
            add(tag, _SCORE_BODY * min(count, 20))

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    for tag, _ in ranked:
        if len(chosen) >= MAX_DERIVED_TAGS:
            break
        push(tag)

    return chosen


# ---------------------------------------------------------------------------
# Per-category defaults
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------


def _upgrade_v1_to_v2(fields: dict, body: str) -> tuple[dict, str]:
    """v1 -> v2: introduce schema_version + the Schema v2 field set.

    Files from the pre-v2 era didn't carry `schema_version`. Everything else
    the migrator already enforces (ALLOWED_KEYS, REQUIRED, FIXED_MODELS,
    distinguishes_from/disambiguation) is applied in apply_defaults, so all
    this upgrade has to do is stamp the version. Kept as a function so the
    registry pattern is established for future bumps.
    """
    fields["schema_version"] = "2"
    return fields, body


# Registered as (from_version, to_version) -> upgrade_fn. Upgrade functions
# take (fields, body) and return (fields, body) -- they may mutate both.
MIGRATIONS: dict[tuple[str, str], "callable"] = {
    ("1", "2"): _upgrade_v1_to_v2,
}


def upgrade_to_current(fields: dict, body: str) -> tuple[dict, str, list[str]]:
    """Walk the migration chain from the file's declared version to CURRENT.

    Returns (fields, body, applied) where `applied` is the list of
    version transitions actually executed (e.g. ['1->2']). Empty when the
    file was already current.
    """
    applied: list[str] = []
    current = str(fields.get("schema_version") or "1")
    while current != CURRENT_SCHEMA_VERSION:
        nxt = None
        for (frm, to), fn in MIGRATIONS.items():
            if frm == current:
                fn(fields, body)
                nxt = to
                applied.append(f"{frm}->{to}")
                break
        if nxt is None:
            raise RuntimeError(
                f"No migration path from schema_version={current!r} to "
                f"{CURRENT_SCHEMA_VERSION!r}. Add a MIGRATIONS entry."
            )
        current = nxt
    fields["schema_version"] = CURRENT_SCHEMA_VERSION
    return fields, body, applied


def apply_defaults(fields: dict, category: str, filename_slug: str, body: str = "") -> dict:
    # Walk the version chain first so downstream defaults see a Schema-v2 dict.
    upgrade_to_current(fields, body)
    fields.setdefault("name", filename_slug)
    fields.setdefault("description", fields.get("vibe") or f"{filename_slug} agent.")
    fields["category"] = category
    if "protocol" not in fields:
        fields["protocol"] = "strict" if category in STRICT_CATEGORIES else "persona"
    if "readonly" not in fields:
        fields["readonly"] = True if category in STRICT_CATEGORIES else False
    if "is_background" not in fields:
        fields["is_background"] = filename_slug == "bg-regression-runner"

    # Model tier: strongest signal wins.
    #   1. FIXED_MODELS hard override for a specific slug.
    #   2. Category default from PER_CATEGORY_MODEL.
    #   3. Existing explicit value (only if it's legal).
    #   4. 'inherit' fallback.
    fixed = FIXED_MODELS.get(filename_slug)
    if fixed:
        fields["model"] = fixed
    elif category in PER_CATEGORY_MODEL:
        existing = fields.get("model")
        # Preserve a pre-existing valid value if it differs from the default --
        # respects hand-curation future contributors may do outside FIXED_MODELS.
        if existing in MODEL_TIERS and existing != "inherit":
            fields["model"] = existing
        else:
            fields["model"] = PER_CATEGORY_MODEL[category]
    else:
        fields.setdefault("model", "inherit")
    if fields.get("model") not in MODEL_TIERS:
        fields["model"] = "inherit"

    existing_tags = fields.get("tags")
    if not isinstance(existing_tags, list):
        existing_tags = None
    fields["tags"] = derive_tags(
        name=filename_slug,
        description=str(fields.get("description") or ""),
        category=category,
        existing=existing_tags,
        body=body,
    )

    # Domains: FIXED > per-category > existing (re-validated) > ['all'].
    domains: list[str] = []
    if filename_slug in FIXED_DOMAINS:
        domains = list(FIXED_DOMAINS[filename_slug])
    elif category in PER_CATEGORY_DOMAINS:
        domains = list(PER_CATEGORY_DOMAINS[category])
    else:
        existing_d = fields.get("domains")
        if isinstance(existing_d, list) and existing_d:
            domains = [str(d) for d in existing_d]
        else:
            domains = ["all"]
    # Filter through vocab; drop unknowns. Deduplicate. Never empty.
    filtered = [d for d in domains if d in ALLOWED_DOMAINS]
    if not filtered:
        filtered = ["all"]
    # Canonical order: all first when present, then sorted alpha.
    fields["domains"] = (["all"] if "all" in filtered else []) + sorted(d for d in filtered if d != "all")
    for bf in BOOL_FIELDS:
        if isinstance(fields.get(bf), str):
            fields[bf] = fields[bf].lower() == "true"

    # Freshness metadata. Setdefault keeps the migrator idempotent: once an
    # agent carries explicit version / updated_at values, the stamp step
    # is a no-op and hand-curated values survive re-migration. Refused
    # values that don't look like what the linter / freshness scanner
    # expect fall through to the baseline so a broken hand-edit can't
    # lock the agent into an un-parseable state.
    ver = str(fields.get("version") or "").strip()
    if not re.match(r"^\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?$", ver):
        fields["version"] = BASELINE_VERSION
    else:
        fields["version"] = ver
    uat = str(fields.get("updated_at") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}", uat):
        fields["updated_at"] = BASELINE_UPDATED_AT
    else:
        fields["updated_at"] = uat[:10]
    return fields


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------


DEFERENCE_MARKER = "<!-- precedence: project-agents-md -->"
DEFERENCE_BLOCK = (
    DEFERENCE_MARKER + "\n"
    "> Project `AGENTS.md` (Invariants / Platform Stack / Modules) overrides\n"
    "> any advice in this persona. When they conflict, follow the project\n"
    "> rules and surface the conflict explicitly in your response.\n"
)


def _ensure_deference_block(body: str, category: str, protocol: str) -> str:
    """Insert the project-precedence block near the top of a persona body.

    Strict agents (orchestration / review) are already protocol-bound --
    they don't need the marker. Persona agents get one; idempotent.
    """
    if protocol != "persona":
        return body
    if DEFERENCE_MARKER in body:
        return body

    lines = body.splitlines(keepends=True)
    # Prefer to insert right after the first level-1 OR level-2 heading
    # (keeps it visually near the "intro" of the agent body).
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("# ") or line.startswith("## "):
            # Skip through the heading + at most one blank line after it.
            insert_at = i + 1
            while insert_at < len(lines) and not lines[insert_at].strip():
                insert_at += 1
            break
    block = DEFERENCE_BLOCK + "\n"
    return "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])


def process_file(path: Path, category: str, changes: list, dry_run: bool) -> None:
    raw = path.read_text()
    if not raw.startswith("---\n"):
        return  # not an agent file
    end = raw.find("\n---\n", 4)
    if end == -1:
        print(f"ERROR {path}: no closing frontmatter delimiter", file=sys.stderr)
        return
    fm_raw = raw[4:end]
    body = raw[end + 5 :]

    fields, order = parse_frontmatter(fm_raw)

    # Drop any keys not in the allowed schema. Record dropped keys so operators
    # can audit and decide whether to move content into the body.
    dropped = [k for k in list(fields.keys()) if k not in ALLOWED_KEYS]
    for k in dropped:
        fields.pop(k, None)
    order = [k for k in order if k in ALLOWED_KEYS]

    filename_slug = path.stem
    # Tag derivation uses the body MINUS the deference block -- otherwise
    # the words inside the block (e.g. "Platform Stack") would match tag
    # synonyms and pollute the agent's own tags on re-migration.
    body_for_tags = body.replace(DEFERENCE_BLOCK, "")
    fields = apply_defaults(fields, category, filename_slug, body=body_for_tags)

    # Persona agents get a precedence block so the project's AGENTS.md
    # invariants visibly outrank the persona's opinions.
    body = _ensure_deference_block(body, category=category, protocol=fields.get("protocol", ""))

    new_fm = emit_frontmatter(fields, order)
    new_content = "---\n" + new_fm + "\n---\n" + body

    if new_content != raw:
        changes.append(str(path.relative_to(REPO_AGENTS)))
        if dropped:
            print(
                f"  dropped non-schema keys from {path.relative_to(REPO_AGENTS)}: "
                f"{', '.join(dropped)}",
                file=sys.stderr,
            )
        if not dry_run:
            path.write_text(new_content)


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    changes: list[str] = []

    for cat_dir in sorted(REPO_AGENTS.iterdir()):
        if not cat_dir.is_dir():
            continue
        if cat_dir.name in NON_AGENT_DIRS or cat_dir.name.startswith("."):
            continue
        # rglob walks into sub-folders (e.g. game-development/unity/) — the
        # category is still the root directory, not the nearest parent.
        for md in sorted(cat_dir.rglob("*.md")):
            process_file(md, category=cat_dir.name, changes=changes, dry_run=dry_run)

    print(f"{'Would update' if dry_run else 'Updated'} {len(changes)} files.")
    if "--verbose" in argv:
        for c in changes:
            print(f"  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
