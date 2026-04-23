# Harmonist — Guide

## What is this

Portable AI orchestration system for Cursor IDE.
Drop the folder into any project — get a 186-agent unified catalog with
tag-driven routing, persistent memory, and protocol-bound quality gates.

**In numbers:**
- **186 agents** in a single unified pool — browse via `agents/index.json`
- **16 categories** — orchestration, review, engineering, design, testing,
  product, project-management, marketing, paid-media, sales, finance, support,
  academic, game-development, spatial-computing, specialized
- **1 schema** — every agent follows `agents/SCHEMA.md` (Schema v2)
- **3 memory files** — context persists between sessions
- **11 IDE integrations** — Cursor, Claude Code, Copilot, Windsurf, Aider, Kimi,
  Qwen, OpenCode, OpenClaw, Gemini CLI, Antigravity
- **0 runtime dependencies** — pure markdown + two small Python utilities for
  linting and building the index

---

## How it works

### One catalog, tag-based routing

There is no "core" vs "catalog" split. Every agent lives at
`agents/<category>/<slug>.md` with the same frontmatter:

```yaml
name, description, category, protocol, readonly, is_background, model, tags
```

Two protocol tiers, distinguished by frontmatter only:

| Protocol | Categories | Role |
|----------|-----------|------|
| `strict` | `orchestration`, `review` | Orchestration-bound. Structured output. Mandatory review gates. Always `readonly: true`. |
| `persona` | everything else | Free-form specialist with domain depth. Invoked when tags match. |

`agents/index.json` is generated from these files and is the **only** routing
table the orchestrator consults.

### Category counts

> Counts are mirrored from `agents/index.json` (`counts.by_category`);
> `check_pack_health.py` fails if this table disagrees with the index.

| Category | Count | Examples |
|----------|-------|---------|
| Orchestration | 2 | `repo-scout`, `agents-orchestrator` |
| Review | 6 | `security-reviewer`, `code-quality-auditor`, `qa-verifier`, `sre-observability`, `bg-regression-runner`, `wcag-a11y-gate` |
| Engineering | 46 | Backend Architect, SRE, Security Engineer, Solidity, DevOps |
| Design | 8 | UI Designer, Brand Guardian, UX Researcher |
| Testing | 8 | API Tester, Performance Benchmarker, Evidence Collector |
| Product | 5 | Product Manager, Sprint Prioritizer, Trend Researcher |
| Project management | 7 | Project Shepherd, Studio Producer, Experiment Tracker |
| Marketing | 30 | Growth Hacker, SEO Specialist, Social Media, TikTok, Reddit |
| Paid media | 7 | PPC Strategist, Programmatic Buyer, Tracking Specialist |
| Sales | 8 | Outbound Strategist, Deal Strategist, Discovery Coach |
| Finance | 6 | FPA Analyst, Tax Strategist, Investment Researcher |
| Support | 5 | Support Responder, Legal Compliance, Analytics Reporter |
| Academic | 5 | Historian, Psychologist, Anthropologist, Narratologist |
| Game development | 20 | Unity, Unreal Engine, Godot, Roblox, Blender |
| Spatial computing | 6 | visionOS, WebXR, XR Immersive, Metal |
| Specialized | 17 | Blockchain Security Auditor, MCP Builder, Salesforce, ZK |

---

### Workflow protocol

Every task goes through a strict protocol defined in `AGENTS.md`:

```
Pre-Task
  1. Read memory (session-handoff.md)
  2. Load agents/index.json
  3. Run repo-scout when file scope is unclear
  4. Plan: list selected agents + tag matches

Execute
  5. Delegate to write agent (one at a time)
  6. Lint check after each agent

Post-Task
  7. Review gates by category/tag (security, performance, QA)
  8. Run regression agent (is_background: true)
  9. Update memory — session-handoff, decisions, patterns
```

The protocol cannot be skipped. Even for a one-line change.

---

### Memory between sessions

Three files in `.cursor/memory/`:

| File | Purpose |
|------|---------|
| `session-handoff.md` | Current project state. Read at the start of every session. |
| `decisions.md` | Architectural decision log. Append-only. |
| `patterns.md` | Lessons from past tasks. |

Entries linked by **correlation ID** — trace task → decision → lesson.

---

## How to integrate

### Option 1 — Automatic (recommended)

```
1. Copy harmonist/ into your project root
2. Open Cursor → Agent mode
3. Type: "Read harmonist/integration-prompt.md and integrate"
4. After integration — START A NEW CHAT
5. Rules take effect from the first message in the new chat
```

Cursor will:
- Analyze your stack, **determine the project domain**, extract routing tags
- Ask which **roles** will work on the project (engineering, design, product,
  marketing, sales, support, finance, testing, academic) — the filter that
  decides whether non-engineering specialists get installed
- Create a domain-specific `AGENTS.md` (not a template)
- Write **domain-specific invariants** (bank → double-entry; gamedev → frame
  budget; blockchain → on-chain verification)
- Install orchestration + review agents into `.cursor/agents/`
- Query `agents/index.json` by `domains × roles × tags` intersection and pick
  5–20 specialists (size scales with how many roles are active)
- Set up `.cursor/memory/`
- Create `.cursor/rules/protocol-enforcement.mdc` and `project-domain-rules.mdc`

### Option 2 — Manual

1. Copy `AGENTS.md` to project root, fill in your stack
2. Copy `agents/orchestration/` and `agents/review/` into `.cursor/agents/`
3. Browse `agents/index.json`, pick specialists, copy into `.cursor/agents/`
4. Copy `memory/` to `.cursor/memory/`

### Adding specialists later

If the project grows into a new role after integration — e.g. a dev tool
starts needing marketing, a fintech adds a support surface — use the
helper instead of hand-copying:

```bash
python3 harmonist/agents/scripts/install_extras.py --role marketing
python3 harmonist/agents/scripts/install_extras.py --slug design-ux-architect,product-manager
python3 harmonist/agents/scripts/install_extras.py --tag growth,seo --thin
```

It sha-verifies each file against `MANIFEST.sha256`, respects thin
variants, refuses to overwrite customised agents without `--force`, and
merges into `.cursor/pack-manifest.json`.

---

## Advantages

- **Single catalog** — one pool, one schema. No more "core vs community".
- **Data-driven routing** — orchestrator reads `agents/index.json`, never a
  hard-coded list. Add or rename agents without touching `AGENTS.md`.
- **Mandatory quality control** — review gates fire based on tags/categories,
  not agent names. Can't "forget" to run security review.
- **Session memory** — new sessions start by reading past state; AI remembers
  what was done, what decisions were made, what rakes to avoid.
- **Rollback on failure** — multi-step tasks that fail midway trigger LIFO
  rollback, not "keep going".
- **Schema-validated** — `lint_agents.py` rejects malformed frontmatter on CI.
- **Zero runtime deps** — pure markdown + two standalone Python scripts.
- **Portability** — one folder, one prompt. Drop → integrate → work.

---

## Folder structure

```
harmonist/
├── GUIDE_EN.md               ← you are here
├── README.md                 ← full documentation
├── AGENTS.md                 ← orchestrator template
├── integration-prompt.md     ← auto-integration prompt
│
├── memory/                   ← memory templates
├── playbooks/                ← NEXUS phases, runbooks, coordination
│
└── agents/                   ← 186 agents in one pool
    ├── SCHEMA.md             ← frontmatter contract
    ├── index.json            ← generated routing table
    │
    ├── orchestration/        ← strict, readonly (2)
    ├── review/               ← strict, readonly (6)
    ├── engineering/          ← persona (46)
    ├── marketing/            ← persona (30)
    ├── game-development/     ← persona (20)
    ├── specialized/          ← persona (17)
    ├── …10 more categories
    │
    ├── templates/            ← blank starters
    ├── integrations/         ← Cursor, Claude Code, Copilot, …
    └── scripts/              ← migrate / build-index / lint / convert / install
```
