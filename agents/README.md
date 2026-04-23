# Agents — Unified Pool

All agents live here under a single flat taxonomy. There is no `core/` layer
and no `catalog/` layer — every agent follows the same schema and is
discoverable through the generated index.

---

## Layout

```
agents/
├── README.md           ← you are here
├── SCHEMA.md           ← frontmatter contract (Schema v2)
├── index.json          ← generated routing table (186 agents)
│
├── orchestration/      strict, readonly   (2 agents)
├── review/             strict, readonly   (6 agents)
├── engineering/        persona            (46 agents)
├── design/             persona            (8)
├── testing/            persona            (8)
├── product/            persona            (5)
├── project-management/ persona            (7)
├── marketing/          persona            (30)
├── paid-media/         persona            (7)
├── sales/              persona            (8)
├── finance/            persona            (6)
├── support/            persona            (5)
├── academic/           persona            (5)
├── game-development/   persona            (20)   ← grouped by engine in sub-folders
├── spatial-computing/  persona            (6)
├── specialized/        persona            (17)
│
├── templates/          ← blank starters for writing new agents
├── integrations/       ← per-IDE adapters (Cursor, Claude Code, Copilot, …)
└── scripts/            ← migrate / build-index / lint / convert / install
```

---

## Two protocol tiers

Every agent declares its tier in the frontmatter:

| Protocol | Categories | Meaning |
|----------|-----------|---------|
| `strict` | `orchestration`, `review` | Orchestration-bound. Structured output, mandatory review gates, always `readonly: true`. |
| `persona` | everything else | Free-form specialist with domain depth and personality. Invoked when `tags`/`category` matches a task. |

The orchestrator applies review gates based on `category: review` + `tags`
(e.g. `tag:security`, `tag:performance`, `tag:qa`). Renaming or adding an
agent requires no changes to the orchestrator — it reads `index.json`.

---

## Schema v2 (frontmatter contract)

Full spec: [`SCHEMA.md`](SCHEMA.md).

```yaml
---
# required
name: engineering-security-engineer
description: Expert application security engineer for threat modeling, vulnerability assessment, and secure code review.
category: engineering
protocol: persona                 # or 'strict'
readonly: false
is_background: false
model: inherit
tags: [engineering, security, threat-modeling, vulnerability-assessment, code-review, audit]

# optional
domains: [all]                    # or: [fintech], [gamedev], etc.
color: red
emoji: 🔒
vibe: Models threats, reviews code, designs security architecture that holds under adversarial pressure.
tools: WebFetch, WebSearch, Read, Write, Edit
author: optional-attribution
---
```

The linter (`scripts/lint-agents.sh`) rejects any markdown file that violates
the schema, and `scripts/build_index.py --check` fails CI if `index.json` is
stale.

---

## Working with the pool

| Task | Command |
|------|---------|
| Validate every agent against Schema v2 | `./scripts/lint-agents.sh` |
| Regenerate `index.json` after edits | `python3 scripts/build_index.py` |
| Check the index is up to date (CI) | `python3 scripts/build_index.py --check` |
| Upgrade agent frontmatter to Schema v2 | `python3 scripts/migrate_schema.py` (idempotent) |
| Emit per-tool integration files | `./scripts/convert.sh` (or `--tool <name>`) |
| Install into Cursor / Claude / Copilot / … | `./scripts/install.sh` |

---

## Querying the index

`index.json` is the single source of truth for routing. Top-level keys:

- `counts` — total / by_category / by_protocol
- `agents` — sorted array of every agent's slug, name, description,
  category, protocol, readonly, is_background, model, tags, domains, path
- `by_category` — `{ category: [slug, …], … }`
- `by_tag` — `{ tag: [slug, …], … }`

Example queries the orchestrator performs:

```python
# "I need a security reviewer for auth code"
agents = index["by_category"]["review"]
# intersect with tag
agents = [s for s in agents if "security" in get_tags(s)]

# "I need a backend specialist for Postgres"
agents = set(index["by_tag"].get("backend", [])) & set(index["by_tag"].get("postgres", []))
```

No hard-coded agent name lists. Ever.

---

## Adding a new agent

1. Start from a file in [`templates/`](templates/) or the closest existing
   agent in the category you're targeting.
2. Write the frontmatter by hand (preferred) or run the migrator afterwards.
3. Write a focused body: identity, mission, rules, output contract.
4. Run `./scripts/lint-agents.sh` — fix any errors.
5. Run `python3 scripts/build_index.py` to refresh `index.json` and commit it.
6. Run `./scripts/convert.sh` if you want integrations regenerated.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution process.

---

## Integrations

Converters under [`integrations/`](integrations/) target:

```
Cursor · Claude Code · GitHub Copilot · Windsurf · OpenCode
Aider · Kimi · Qwen · Gemini CLI · Antigravity · OpenClaw
```

Run `./scripts/convert.sh --tool all` to regenerate, then
`./scripts/install.sh` (interactive) to drop files into the right spots.

Per-tool details live in each subdirectory's `README.md`.
