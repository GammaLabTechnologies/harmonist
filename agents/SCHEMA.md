# Agent Schema v2

> Single source of truth for the shape of every agent in this pack.
> One schema, one pool — `agents/index.json` is generated from these files,
> and the orchestrator routes tasks to agents via that index.
>
> **See also**: `agents/STYLE.md` — how the body of an agent should *read*
> (mission shape, critical rules, Deep Reference convention, retrofit
> checklist). SCHEMA enforces the frontmatter contract; STYLE shapes the
> prose.

---

## File layout

Every agent lives at `agents/<category>/<slug>.md` and has three parts:

```
---
<YAML frontmatter — required fields + optional fields>
---

<Markdown body — the agent's system prompt>
```

Anything under `agents/` that is **not** an agent file (README, SCHEMA, index.json,
playbooks, templates, scripts) MUST NOT start with `---` as its first line.
The linter uses the first-line `---` check to discriminate.

---

## Frontmatter — required

Every field below is mandatory. Missing any → lint error.

| Field | Type | Values | Meaning |
|-------|------|--------|---------|
| `schema_version` | enum (string) | currently `"2"` | Declares which version of this schema the file conforms to. Linter rejects unknown / outdated versions; `scripts/migrate_schema.py` rolls older versions forward automatically. See the "Schema versioning" section at the bottom of this doc. |
| `name` | string | human-readable display name | Shown in integrations, UI surfaces, and the agent's self-reference in the body. May be kebab-case (e.g. `qa-verifier` for strict reviewers) or human-formatted (e.g. `Backend Architect`, `DevOps Automator` for persona agents). Does NOT have to match the filename. The identity key used for routing, state, and hooks is the **slug** — see below. |
| `description` | string | 1–2 sentences | What it does, when to invoke it. Used by the orchestrator for routing. |
| `category` | enum | see list below | Top-level bucket. Must match the parent directory. |
| `protocol` | enum | `strict` \| `persona` | How the agent behaves (see "Protocol" section). |
| `readonly` | bool | `true` \| `false` | `true` = reviewer/scout, cannot edit files. `false` = write agent. |
| `is_background` | bool | `true` \| `false` | `true` = long-running (tests, lint, builds). Default `false`. |
| `model` | enum | `fast` \| `inherit` \| `reasoning` | Which model tier the host should use. See "Model tiers" below. |
| `tags` | list[string] | lowercase kebab | Searchable labels used by `index.json` for task-to-agent matching. |

### Slug — identity key (derived, not frontmatter)

The **slug** is the agent's stable identity across the whole system:
routing, `index.json` lookups, hook `AGENT: <slug>` markers, memory
entries, telemetry, `distinguishes_from` references. It is NOT a
frontmatter field — it is the filename stem (`agents/<category>/<slug>.md`
without the `.md` extension).

Rules:

- Must match `[a-z0-9][a-z0-9-]*` (lowercase, kebab-case).
- Must be unique across the entire `agents/` tree (enforced by the linter).
- Chosen once at creation time; renaming a slug is a breaking change
  because every consumer that cites it (other agents' `distinguishes_from`,
  hook AGENT markers in logs, cached memory entries) must be updated.
- Catalog agents imported from upstream libraries keep their
  `<category>-<slug>` prefix (e.g. `engineering-backend-architect.md`)
  to preserve recognizability.

The orchestrator uses the slug; `name` is cosmetic.

### Category enum

```
orchestration      — task routing, repo mapping, delegation
review             — readonly reviewers (security, quality, qa, sre, regression)
engineering        — backend, frontend, devops, data, embedded, AI engineering
design             — UI/UX, brand, accessibility, visual
testing            — QA, performance, API testing, evidence
product            — PM, sprints, feedback, trends
project-management — planning, studio ops, coordination
marketing          — growth, SEO, content, social, localization
paid-media         — PPC, tracking, campaign audits
sales              — outbound, deals, discovery, proposals
finance            — FPA, bookkeeping, tax, investments
support            — customer support, compliance, analytics
academic           — research, psychology, history
game-development   — Unity, Unreal, Godot, Roblox, Blender
spatial-computing  — XR, visionOS, WebXR
specialized        — blockchain, MCP, Salesforce, ZK, niche
```

### Tags — recommended vocabulary

Use these when they apply. Add new ones freely — the index just aggregates
whatever is there. Prefer 3–8 tags per agent.

```
# Skill tags
backend frontend fullstack devops infra database cache messaging
security auth authz secrets owasp crypto
testing qa e2e unit performance load observability slo

# Domain tags
fintech banking payments escrow wallet
blockchain ethereum solana ton evm zk
gamedev unity unreal godot
healthcare hipaa pci gdpr
ai ml llm rag embedding
mobile ios android react-native
web react vue angular next svelte
saas b2b multi-tenant

# Function tags
review audit scout orchestration writer reviewer
```

---

## Frontmatter — optional

| Field | Type | When to use |
|-------|------|-------------|
| `domains` | list[string] | Project types where the agent is relevant. Default `[all]`. Members MUST be in the controlled vocabulary (see "Domain vocabulary" below). Used by the integration prompt to filter out agents irrelevant to the current project (e.g. a TON project hides WeChat / Xiaohongshu specialists). |
| `distinguishes_from` | list[string] | Slugs of agents that are often confused with this one. Used by the orchestrator as a tie-breaker when tag intersection alone is ambiguous. Every entry MUST be a valid slug under `agents/`; self-reference is rejected by the linter. |
| `disambiguation` | string | One-line guidance (≤ 240 chars) that tells the orchestrator when to pick THIS agent versus the ones in `distinguishes_from`. Written as "Use me for X; for Y delegate to `<slug>`." |
| `version` | string | SemVer tag for the agent body (`MAJOR.MINOR[.PATCH][-pre]`). Bump on meaningful content changes. `scripts/migrate_schema.py` stamps `1.0.0` on any file missing this field so `scan_agent_freshness.py` has baseline data to reason about aging. |
| `updated_at` | string | `YYYY-MM-DD` — date of the last meaningful content change to the agent body. Checked by `scan_agent_freshness.py --stale-after-days N`. The migrator stamps a baseline date on any file missing this field; hand-curated dates are preserved on re-migration. |
| `deprecated` | bool \| string | `true` or a one-line replacement hint. `scan_agent_freshness.py` errors out if any agent is marked deprecated and the slug is still in the install set. |
| `color` | string | CSS color name or `#RRGGBB`. Used by `integrations/opencode` and UI surfaces. |
| `emoji` | string | Single emoji for display. Used by `integrations/openclaw` IDENTITY.md. |
| `vibe` | string | Short persona tagline (≤ 140 chars). |
| `tools` | string | Comma-separated tool list that the agent is allowed to call (used by Qwen). |
| `author` | string | Original author, when attribution matters. |

Optional fields are preserved by converters but never required by the core
orchestrator.

---

## Model tiers

`model:` is a three-valued enum that tells the host which model class to
route the agent through. Setting it wrong wastes money OR loses quality;
the migrator chooses defaults per category and hard-overrides a list of
protocol-critical agents.

| Tier | When | Typical slots |
|------|------|---------------|
| `fast` | Mechanical, narrow tasks where a smaller model is sufficient. | `repo-scout`, `bg-regression-runner`, content generators, short-form copy, prompt engineering. |
| `inherit` | The task size varies wildly — match whatever the host session is already using. | Most write agents; sensible default. |
| `reasoning` | Deep analysis / decisions / audits where getting it wrong costs real money. | All strict reviewers (`security-reviewer`, `code-quality-auditor`, `qa-verifier`, `sre-observability`), architects, auditors, threat modellers, deep marketing strategists. |

### How tiers are assigned

1. **`FIXED_MODELS` in `scripts/migrate_schema.py`** — per-agent hard
   override. Reserved for strict protocol agents and hand-curated
   exceptions (e.g. `marketing-growth-hacker` is `reasoning` even though
   its category default is `fast`).
2. **`PER_CATEGORY_MODEL`** — category-level default. Applied when there
   is no per-agent override.
3. **Existing explicit value** — if an agent file already declares a
   non-`inherit` model (legacy or hand-curated), it is preserved.
4. **Fallback** — `inherit`.

Add a tag or change a default only when the new default would genuinely
apply to every agent in the category, not to pacify one outlier.

## Protocol values

### `protocol: strict`

Reserved for agents that participate in the orchestration contract:
- Structured output (plan / evidence / verdict or similar deterministic shape).
- Mandatory review-gate semantics (e.g. `qa-verifier` must run before "done").
- Short, reference-style prompts — no flavor text.
- `readonly: true` in almost all cases (reviewers and scouts).
- Model tier often matters (`fast` for scouts, `reasoning` for audits).

Examples: `repo-scout`, `security-reviewer`, `qa-verifier`, `sre-observability`,
`code-quality-auditor`, `bg-regression-runner`.

### `protocol: persona`

Free-form specialist prompts with personality, domain depth, and opinionated
heuristics. Most catalog agents are persona. They can be invoked standalone
or from the orchestrator when the routing table matches a `tag`/`category`.

Examples: `engineering-security-engineer`, `marketing-growth-hacker`,
`game-development-unity-engineer`.

---

## Body — minimum requirements

The Markdown body (everything after the closing `---`) must contain:

1. **Identity** — one line declaring who the agent is.
2. **Core mission / responsibilities** — what it does, non-negotiably.
3. **Rules or guardrails** — concrete constraints (e.g. "never use float for money").

Persona agents typically extend this with:
- Tone / communication style
- Workflow / deliverables
- Signature questions or frameworks

Strict agents extend with:
- Input contract
- Output contract (exact shape)
- Escalation criteria

Body must be ≥ 50 words to be considered meaningful.

### `## Deep Reference` convention (for long persona agents)

Persona agents can grow to 300–500 lines of examples, frameworks, and
scenarios. Loading all of that into context on every Task invocation is
wasteful. Split the body:

```markdown
# Agent Name

## Identity
...one line...

## Core Mission
...2–3 sentences...

## Critical Rules
1. ...
2. ...

## Output contract
...

<!-- Everything the orchestrator + subagent need for a typical task
     lives above this line. Keep it under ~80 lines. -->

## Deep Reference

## Framework X
...detailed playbook...

## Scenario: Y
...example walkthrough...

## Framework Z
...
```

The `## Deep Reference` header is the cut point. Everything **before** it
is the agent's *essentials* — pulled into every invocation. Everything
**after** is the *deep reference* — pulled only when the subagent
explicitly determines it needs the detail.

Tooling:

| Tool | Behaviour |
|------|-----------|
| `scripts/extract_essentials.py` | Write a `.essentials.md` variant of any agent, containing frontmatter + body up to `## Deep Reference` (or the first `## ` heading past a 80-line budget if the marker is absent). |
| `scripts/convert.sh --thin` | Run the converter using essentials only; produced integration files stay under a sensible token budget. |
| `scripts/lint_agents.py` | Warns (not errors) when a persona agent's non-blank body exceeds 200 lines with no `## Deep Reference` marker. |

Strict agents already fit in < 50 lines; they do not use this convention.

---

## Example — strict reviewer

```yaml
---
name: security-reviewer
description: Reviews diffs for security vulnerabilities, OWASP Top 10, secrets, auth/authz flaws. Use after auth, payments, admin, secrets, or external API changes.
category: review
protocol: strict
readonly: true
is_background: false
model: inherit
tags: [security, owasp, secrets, auth, review]
domains: [all]
---

You are a paranoid senior application security reviewer. Review from an attacker
perspective.

## OWASP Top 10 Checklist
...
```

## Example — persona specialist

```yaml
---
name: engineering-security-engineer
description: Expert application security engineer for threat modeling, vulnerability assessment, secure code review, and security architecture.
category: engineering
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [security, threat-modeling, vulnerability-assessment, code-review, audit]
domains: [all]
color: red
emoji: 🔒
vibe: Models threats, reviews code, hunts vulnerabilities, designs security architecture that holds under adversarial pressure.
---

# Security Engineer Agent

You are **Security Engineer**, an expert application security engineer...
```

---

## Routing contract

The orchestrator (`AGENTS.md`) does NOT hard-code agent names.
It reads `agents/index.json`, matches the task against `tags` / `category` / `description`,
and picks the right agent. Review gates apply based on `protocol: strict`
and `category: review`.

Routing rule of thumb:

```
category: review         → invoked by protocol (always runs after write)
category: orchestration  → invoked first (repo-scout)
other categories         → invoked by tag/description match against task
```

---

## File naming

- Directory = `category` (exact match).
- Filename = `<slug>.md` (the slug is the identity key — see "Slug — identity
  key" in the required-frontmatter section).
- Slug rules: `[a-z0-9][a-z0-9-]*`, no spaces, no uppercase, hyphens between
  words. The linter enforces this against the filename stem.
- Frontmatter `name` is a human-readable display name and does **not** have
  to equal the slug. Strict/review agents conventionally keep `name == slug`
  (e.g. `name: qa-verifier`); persona agents commonly use a prose form
  (e.g. `name: Backend Architect`). Either is valid.
- Catalog agents coming from upstream libraries keep their `<category>-<slug>`
  prefix (e.g. `engineering-backend-architect.md`) to preserve recognizability.

---

## Linter

`scripts/lint-agents.sh` enforces:

1. First line `---`, closing `---` present.
2. All required frontmatter fields present.
3. `category` value matches parent directory.
4. Slug (filename stem) matches `[a-z0-9][a-z0-9-]*`, is unique across the
   pool, and `name` is a non-empty string. `name` is free-form — it does
   NOT have to equal the slug (see "Slug — identity key" and "File naming").
5. `protocol` is `strict` or `persona`.
6. `readonly`, `is_background` are bool.
7. `tags` is a non-empty list.
8. Body ≥ 50 words.
9. Warns (not errors) when a persona agent's non-blank body exceeds 200
   lines with no `## Deep Reference` marker (the `--thin` converter
   falls back to a budget heuristic without one).

Pre-existing persona fields (`color`, `emoji`, `vibe`) are only warned on missing values, never required.

---

## Domain vocabulary

The allowed values for `domains:` are kept in
`scripts/migrate_schema.py::ALLOWED_DOMAINS`.

| Domain | When to use |
|--------|-------------|
| `all` | Default. Universal agent, useful regardless of project shape. |
| `china-market` | Targets the Chinese market (Baidu, WeChat, Xiaohongshu, Weibo, Douyin, Kuaishou, Zhihu, Bilibili, Feishu, China e-commerce). Hidden from projects that declare `domains: [<anything but all or china-market>]`. |
| `korean-market` | Korean business practices and platforms. |
| `french-market` | French consulting ecosystem (ESN, portage salarial). |
| `fintech` | Payments, banking, ledger, escrow. |
| `blockchain` | Smart contracts, DeFi, NFT, crypto — agent expects an on-chain context. |
| `gamedev` | Game engines (Unity, Unreal, Godot, Roblox, Blender) and related crafts. |
| `xr` | Extended reality — visionOS, WebXR, AR/VR. |
| `healthcare` | HIPAA / PHI / medical-regulatory context. |
| `academic` | Research-style deep analysis (history, psychology, anthropology, narratology). |
| `gov-tech` | Government digital / public-sector-specific. |
| `education` | Education-vertical specialists (e.g. study-abroad). |

Agents with two genuine contexts may list both (`[china-market, healthcare]`
for the China healthcare marketing compliance specialist).

How the value is assigned:

1. **`FIXED_DOMAINS`** in `migrate_schema.py` — hand-curated per-slug
   override for agents whose scope is sharply non-universal.
2. **`PER_CATEGORY_DOMAINS`** — whole categories whose agents share a
   domain (`game-development` → `[gamedev]`, `spatial-computing` →
   `[xr]`, `academic` → `[academic]`).
3. **Existing value** — re-validated against vocab.
4. **Fallback** — `[all]`.

### Routing

`scripts/build_index.py` emits a `by_domain` bucket in `index.json`.
The integration prompt (Step 2) extracts the project's domain from the
project brief and filters picks in Step 5 so only
`domains ⊆ (project_domains ∪ {all})` surface.

Adding a new domain means adding one entry to `ALLOWED_DOMAINS` and
updating this table. Keep the list small — every domain is a filter
axis the integrator has to understand.

---

## Schema versioning

Each agent carries `schema_version: "<N>"` in its frontmatter. The
tooling keeps three constants in sync:

| Constant | Defined in | Purpose |
|----------|-----------|---------|
| `CURRENT_SCHEMA_VERSION` | `scripts/migrate_schema.py` | The version this tooling emits / validates as authoritative. Current: `"2"`. |
| `KNOWN_SCHEMA_VERSIONS` | `scripts/migrate_schema.py` | Every version the linter recognizes. Unknown versions = hard error. |
| `MIGRATIONS` | `scripts/migrate_schema.py` | Registry of `(from, to) -> upgrade_fn` pairs. Used by the migrator to roll files forward. |

### Running the migrator

`python3 scripts/migrate_schema.py` walks each file's declared
`schema_version` forward through the `MIGRATIONS` chain until it
reaches `CURRENT_SCHEMA_VERSION`. A file already on current is a no-op;
a file on an older version is rewritten in place. No networking, no
destructive operations — just deterministic transforms.

### Introducing a breaking change (bumping to N+1)

1. Implement the new shape in the migrator / linter / index builder.
2. Write `_upgrade_vN_to_vN+1(fields, body) -> (fields, body)` in
   `scripts/migrate_schema.py`.
3. Register it: `MIGRATIONS[("N", "N+1")] = _upgrade_vN_to_vN+1`.
4. Bump `CURRENT_SCHEMA_VERSION` to `"N+1"` and add `"N+1"` to
   `KNOWN_SCHEMA_VERSIONS`. Keep `"N"` in `KNOWN_SCHEMA_VERSIONS` until
   the roll-forward has landed in every agent file of every consumer.
5. Run the migrator; commit the resulting diff.

NEVER delete upgrade functions for older versions — they are the only
path for old forks (integration pack copied into a project months ago)
to catch up.

### Minor / non-breaking additions

Adding a new optional field, a new tag to `tags.json`, or a new lint
warning does NOT require a schema-version bump. Bump only when an
existing file would fail validation under the new rules.
