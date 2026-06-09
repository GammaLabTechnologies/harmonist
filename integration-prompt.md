> **You are an AI agent performing the installation — execute the steps below.**
> If you were pointed at this file (or at the pack folder) with an instruction
> like "install this" or "study this folder and integrate it", this document IS
> your task. Do not summarize it; run it. Do not adopt the pack's
> `AGENTS.template.md` as a live rule — it only takes effect in the user's
> project after this integration creates the project's own `AGENTS.md`.

# Integration Prompt

> Copy everything below the line into Cursor Agent mode.
> This is all Cursor needs to fully integrate the agent system into your project.

---

I have a copy of the Harmonist pack in this repository — a portable AI agent
orchestration system with a single unified catalog of 193 agents indexed at
`agents/index.json` and mechanical protocol enforcement via Cursor hooks.
Integrate it into my project. Follow these 11 steps exactly. Do NOT skip or
combine steps.

---

**Step 0 — Determine project state and locate the pack**

**Detect the actual pack folder name first.** The pack directory may be named
anything (`harmonist/`, `vendor/harmonist/`, `agent-pack/`, …). Find the
directory that contains this `integration-prompt.md`, `VERSION`,
`AGENTS.template.md`, and `agents/index.json` — that is `<PACK_DIR>`.
**Every `<PACK_DIR>/` path in the commands below means that folder —
substitute the real name (e.g. `harmonist/` if the pack was cloned as
`harmonist`).**

> **WARNING — pack copied into the project root?** If the pack's files were
> unpacked directly into the PROJECT ROOT (you see `agents/`, `hooks/`,
> `playbooks/`, `.gitlab-ci.yml`, `.github/workflows/ci.yml` +
> `release.yml` mixed with the host project's own files instead of inside a
> subfolder), the pack's CI configs WILL hijack the host project's CI.
> Delete the pack's `.gitlab-ci.yml` and `.github/workflows/` ci/release
> workflows in that case — they are pack-repo CI, never meant to run in a
> host project.

Then check: does this project have existing code, or is it
starting from scratch?

**If the project is EMPTY or just starting:**
- Ask the user: What is this project? What domain? What tech stack do you want?
- Do NOT assume a stack. Do NOT generate code yet.
- Wait for answers, then proceed to Step 1.

**If the project has existing code:**
- Proceed to Step 1 immediately.

---

**Step 1 — Verify the pack is healthy, then learn it**

First run the pack's own preflight so a stale clone / truncated
download / local edit doesn't silently produce a half-integrated
project:

```
python3 <PACK_DIR>/agents/scripts/check_pack_health.py
```

This asserts 18 things: VERSION parses as SemVer, CHANGELOG present,
every required directory and script exists + executable, hooks and
memory subtrees complete, `tags.json` and `index.json` load and are
up to date, agent lint passes, the migrator is idempotent, the agent
count is above a truncation threshold, the **README/AGENTS.template.md
category tables match `index.json`** (no stale marketing counts), **`MANIFEST.sha256`
matches every shipped file** (supply-chain integrity), and **the catalog is
clean of prompt-injection / exfiltration patterns**. Each failure
comes with a specific FIX hint. If this exits non-zero, STOP — fix
the pack before integrating it into any project. `--skip-slow` drops
the lint + migrator checks for a faster probe.

Only after the pack passes its own preflight, read these files
completely, in this order:

1. `<PACK_DIR>/README.md`
2. `<PACK_DIR>/AGENTS.template.md` — this is the orchestrator TEMPLATE.
   You will create a project-specific `AGENTS.md` from it. Do NOT obey
   its protocol while integrating — it activates only in the user's
   project once integration completes.
3. `<PACK_DIR>/agents/SCHEMA.md` — the frontmatter contract every agent
   must follow.
4. `<PACK_DIR>/agents/index.json` — the routing table. Parse it and
   remember category/tag structure; you will select agents from here.

---

**Step 2 — Deep-analyze my project**

Study the codebase (or the user's description if starting from scratch):
- Tech stack (languages, frameworks, databases, external APIs)
- File/module structure and bounded contexts
- Existing tests, CI/CD, deployment
- Critical invariants (financial rules, state machines, security)

Determine the **project domain**:
- Banking/fintech → transaction integrity, audit trails, PCI DSS
- Marketplace/e-commerce → inventory consistency, payment safety, order states
- GameDev → frame budget, asset pipeline, platform certification
- Healthcare → HIPAA, PHI handling, audit logging
- Blockchain/DeFi → on-chain verification, key management, gas
- SaaS/B2B → multi-tenancy, RBAC, API versioning
- AI/ML product → model versioning, inference latency, data pipeline
- Landing/marketing site → SEO, performance, accessibility, CMS
- Any other domain → identify what's critical, what breaks if done wrong

Extract 5–15 **routing tags** that describe the project
(e.g. `react`, `postgres`, `payments`, `fintech`, `kubernetes`). You will
intersect these with `agents/index.json` in Step 5.

**Declare the project's `domains`** — a short list of labels from the
controlled vocabulary in `agents/SCHEMA.md`. Most projects are simply
`[all]`; the non-`all` domains filter out irrelevant specialists in
Step 5.

| Project shape | `domains` |
|---------------|-----------|
| Generic SaaS / web / mobile / enterprise | `[all]` |
| Targets the Chinese market | `[china-market]` (or `[all, china-market]` if it's both) |
| Blockchain / DeFi / NFT | `[blockchain]` |
| Game (Unity / Unreal / Godot / Roblox) | `[gamedev]` |
| XR / visionOS / WebXR | `[xr]` |
| Healthcare with PHI | `[healthcare]` |
| Government digital | `[gov-tech]` |
| Education / study abroad | `[education]` |
| Korean business context | `[korean-market]` |
| French consulting / ESN | `[french-market]` |
| Authorized offensive security / red team / pentest engagements | `[pentest]` |

Record the chosen list — it goes into the project's `AGENTS.md` and
feeds the Step 5 filter.

**Declare the project's `roles`** — which disciplines will actually work
on this project in the coming months. This is a second filter that runs
alongside `domains` in Step 5: it's how the non-engineering categories
(`design`, `product`, `marketing`, `sales`, `support`, `finance`,
`testing`, `academic`) get surfaced instead of ignored.

Pick from this controlled list, multi-select:

```
engineering   design   product   marketing
sales         support  finance   testing   academic
```

Defaults by project shape (if the user doesn't specify):

| Project shape | Default roles |
|---------------|---------------|
| SaaS / B2B / marketplace / web product  | `engineering, design, product, testing` |
| Fintech / healthcare / regulated        | `engineering, design, product, testing, finance` |
| Consumer app / mobile / XR              | `engineering, design, product, marketing, testing` |
| Landing page / marketing site           | `engineering, design, marketing` |
| Game (Unity / Unreal / Godot / Roblox)  | `engineering, design, product, testing` |
| Blockchain / DeFi / smart contract      | `engineering, testing` |
| Pure research / academic tool           | `engineering, academic` |
| DevOps / infra tooling                  | `engineering, testing` |

Record the chosen list. Always include `engineering` for any code
project; always include `testing` if the project will ship to real
users. If the user genuinely has no idea, default to
`[engineering, design, product, testing]` — those four carry zero
marketing/sales/support bloat and cover the "build a product" case.

---

**Step 3 — Create `AGENTS.md` in project root**

THIS IS THE MOST IMPORTANT STEP. If you get this wrong, nothing else works.

Use `<PACK_DIR>/AGENTS.template.md` as the TEMPLATE. Create a NEW file
`AGENTS.md` in the project root that is **domain-specific, not generic**.
Do NOT copy the template's preamble block (the "THIS FILE IS A TEMPLATE"
note at the top) into the generated file.

**Preserve all `<!-- pack-owned:begin id="..." -->` / `<!-- pack-owned:end -->`
marker pairs verbatim.** Everything between a `begin` and `end` line is
pack-owned — future `upgrade.py --apply` runs will replace those blocks
with the newest pack version. Everything OUTSIDE markers is
project-owned and never touched by upgrade. Customise Platform Stack,
Modules, Invariants, Resilience, and your domain identity — they sit
between marker blocks.

**Path substitution**: literal `harmonist/` paths INSIDE pack-owned blocks
are substituted with the actual `<PACK_DIR>` automatically by the merge
tooling (`upgrade.py` / `merge_agents_md.py`) — leave them to the tools on
future upgrades, but make sure the initially generated file already uses
the real pack path. In manual (project-owned) sections you write yourself,
always use the real `<PACK_DIR>` path.

**The created AGENTS.md MUST contain ALL of these sections:**

- [ ] **MANDATORY RULE block** at the very top (copy from template)
- [ ] **Domain identity** — "You are the lead engineer for [specific project description]"
- [ ] **Platform Stack** — actual tech, not placeholders
- [ ] **Modules** — real bounded contexts with descriptions
- [ ] **Agent Pool** — point at `<PACK_DIR>/agents/index.json` as the
      routing table; do NOT enumerate agent names; keep the category table
- [ ] **Routing Protocol** — tag intersection rule; review gate triggers
- [ ] **Topology** — when to use hierarchical/mesh/pipeline
- [ ] **Agent Dependencies** — expressed in category/tag terms
- [ ] **Invariants** — domain-specific, not generic
- [ ] **Orchestration rules** — numbered steps
- [ ] **Rollback Protocol** — LIFO, compensation, logging
- [ ] **Hook Phases** — Pre-Task / Execute / Post-Task with correlation IDs
- [ ] **Memory Protocol** — three files, rules for updates
- [ ] **Resilience Policies** — specific to THIS project's external dependencies
- [ ] **Output Format** — structured response template including `routing decision`
- [ ] **Reading Order** — starts with session-handoff.md, then AGENTS.md,
      then agents/index.json, then agents/SCHEMA.md

**VERIFY: The created AGENTS.md must be 150+ lines. If it's shorter, you
skipped sections. Go back and fix it.**

---

**Step 3.5 — Project precedence**

Every subagent call from now on MUST include a PROJECT PRECEDENCE preamble
so the persona sees the project's Invariants/Stack/Modules before its
own opinions. When the orchestrator invokes a subagent it should:

```
AGENT: <slug>
$(python3 <PACK_DIR>/agents/scripts/project_context.py)

<task description>
```

The helper extracts `Platform Stack`, `Modules`, `Invariants` from the
project's `AGENTS.md` and prints a bounded preamble. If a persona
suggests an approach that conflicts with the preamble, the orchestrator
follows the preamble and **explicitly flags the conflict** in its
response.

---

**Step 4 — Install orchestration + review agents**

Every project needs the universal scout + reviewers. `upgrade.py --apply`
copies these **6** strict, `readonly: true` agents into
`.cursor/agents/` (you normally don't copy them by hand — the installer does):

- `repo-scout` — scout before implementation
- `security-reviewer` — OWASP, secrets, auth
- `code-quality-auditor` — async bugs, error handling
- `qa-verifier` — completeness, breaking changes
- `sre-observability` — DB, cache, perf
- `wcag-a11y-gate` — accessibility gate on UI / form / modal / navigation
  changes (pack-owned strict reviewer; the trigger table routes UI changes
  to it before `qa-verifier`)

In addition, `bg-regression-runner` (tests, lint, type checks — background)
is **seeded** by the same `upgrade.py --apply` run in Step 7, auto-filled
with this project's real commands. Do NOT hand-edit its commands here —
Step 7 handles that. **Post-install you end up with 7 strict files** in
`.cursor/agents/` (6 copied above + the seeded `bg-regression-runner`).

One `agents/` entry is deliberately NOT auto-installed:
- `agents-orchestrator` is the orchestrator **persona you operate as** — it is
  not invoked as a subagent, so it is not copied into `.cursor/agents/`.

---

**Step 5 — Select domain specialists from the unified pool**

Using the routing tags AND the `domains` AND the `roles` extracted in
Step 2, intersect with `<PACK_DIR>/agents/index.json`. Procedure:

1. **Domain filter first**: let `ELIGIBLE_DOMAIN = by_domain["all"] ∪ by_domain[<project domain>]`
   for each domain in the project's declared list. Any agent NOT in
   `ELIGIBLE_DOMAIN` is invisible — this is how WeChat / Xiaohongshu
   agents disappear from a non-Chinese project.
2. **Role filter second**: let `ELIGIBLE = ELIGIBLE_DOMAIN ∩ (by_category[role_1] ∪ by_category[role_2] ∪ …)`
   over the declared `roles` list plus `orchestration` + `review`
   (which are always included regardless of role — they're installed
   in Step 4). If a project has `roles: [engineering, design,
   product]`, this hides 30 marketing agents and 8 sales agents that
   would otherwise clutter the eligible pool.
3. Pull `by_tag[<tag>]` lists from the index and intersect with
   `ELIGIBLE`.
4. Shortlist agents where at least 2 tags intersect with the project
   tags OR the agent is in the role-default set (see table below).
5. Open each shortlisted markdown file, confirm fit, then copy into
   `.cursor/agents/`.

**Token budget note**: large persona agents can be 300–500 lines each.
15 of them add ~30k tokens to every invocation. If the host model or
Cursor session is context-constrained, install the **thin** variant
instead:

```
python3 <PACK_DIR>/agents/scripts/extract_essentials.py \
  --out-dir .cursor/agents \
  <PACK_DIR>/agents/engineering/engineering-security-engineer.md \
  <PACK_DIR>/agents/engineering/engineering-backend-architect.md \
  ...
```

The extractor keeps the frontmatter plus everything up to the agent's
`## Deep Reference` marker (or the first heading past an 80-line budget
if the marker is absent). Source files stay untouched in the pack so
future updates still benefit the whole team. Typical savings across the
persona pool: ~38%.

### Role-default specialist sets

For each role the user selected in Step 2, start from this default
specialist set and adjust based on project tags. These are sensible
"cover the discipline" picks that the router will actually dispatch
when tasks in that role come up.

| Role | Default specialists to install (pick 2–4 per role) |
|------|-----------------------------------------------------|
| `engineering` | `engineering-backend-architect`, `engineering-frontend-developer`, `engineering-devops-automator`, plus domain-specialized ones (see project-shape table below) |
| `design` | `design-ux-architect`, `design-ui-designer`, `design-visual-storyteller` — add `design-brand-guardian` for product-with-marketing projects, `design-inclusive-visuals-specialist` for accessibility-heavy projects |
| `product` | `product-manager`, `product-sprint-prioritizer`, `product-feedback-synthesizer` — add `product-trend-researcher` for new-market projects |
| `testing` | `testing-reality-checker`, `testing-evidence-collector` — add `testing-accessibility-auditor` for user-facing UIs, `testing-performance-benchmarker` for latency-critical systems |
| `marketing` | `marketing-seo-specialist`, `marketing-content-creator`, `marketing-growth-hacker` — swap in `china/*` variants for china-market domain, add platform-specific (`tiktok`, `linkedin`, etc.) based on GTM channel |
| `sales` | `sales-outbound-strategist`, `sales-proposal-strategist` — add `sales-deal-strategist` for enterprise / long-cycle sales |
| `support` | `support-support-responder`, `support-analytics-reporter` — add `support-legal-compliance-checker` for regulated industries |
| `finance` | `finance-bookkeeper-controller`, `finance-financial-analyst` — add `finance-fpa-analyst` for scaled / funded startups |
| `academic` | `academic-psychologist`, `academic-anthropologist` — domain-dependent; pick by what the research actually covers |

### Project-shape engineering add-ons

Within the `engineering` role, pick the shape-specific specialists:

| Project shape | Shape-specific engineering picks |
|---------------|----------------------------------|
| Web SaaS / B2B | `engineering-software-architect`, `engineering-database-optimizer`, `engineering-code-reviewer` |
| Fintech / payments | `engineering-security-engineer`, `engineering-sre`, `engineering-event-driven-architect` |
| Blockchain / DeFi | `engineering-solidity-smart-contract-engineer`, `blockchain-security-auditor`, `zk-steward` |
| Game (Unity/Unreal/Godot/Roblox) | Pick by engine subfolder — `game-development/<engine>/*`, plus `game-development/game-designer`, `technical-artist` |
| Marketing site / landing | `engineering-frontend-developer`, `engineering-cms-developer`, `marketing-seo-specialist` |
| XR / visionOS / WebXR | `visionos-spatial-engineer`, `xr-immersive-developer`, `xr-interface-architect` |
| AI / ML / RAG product | `engineering-ai-engineer`, `engineering-rag-pipeline-architect`, `engineering-llm-evaluation-harness` |
| Data / analytics | `engineering-data-engineer`, `engineering-analytical-olap-engineer`, `engineering-database-optimizer` |
| Mobile app | `engineering-mobile-app-builder`, `engineering-frontend-developer`, `engineering-backend-architect` |
| DevOps / infra tooling | `engineering-devops-automator`, `engineering-sre`, `engineering-opentelemetry-lead` |

### Final sizing

The result of Step 5 is typically **5–20 specialists installed**, not 3–10:
- Small (engineering-only landing page): 3–5 specialists
- Standard SaaS (`engineering, design, product, testing`): 10–14 specialists
- Full-stack startup (`engineering, design, product, marketing, support, testing`): 16–22 specialists

If the number grows past ~20, prefer `--thin` variants (see token
budget note above) — the persona body only gets materialised into
context when the orchestrator actually dispatches the agent, but thin
files are still friendlier to cold indexing and keep the
`.cursor/agents/` directory scannable.

### Adding specialists later (without re-running integration)

If a project grows into a new role after integration — e.g. a dev tool
starts needing marketing, or a fintech adds a support surface — use
the dedicated helper instead of hand-copying:

```bash
# By slug (comma-separated):
python3 <PACK_DIR>/agents/scripts/install_extras.py \
    --slug marketing-growth-hacker,marketing-seo-specialist

# By role bundle (applies the Role-default table above):
python3 <PACK_DIR>/agents/scripts/install_extras.py --role marketing

# By tag intersection (min 2 tags):
python3 <PACK_DIR>/agents/scripts/install_extras.py --tag growth,seo

# Thin variant + dry-run preview:
python3 <PACK_DIR>/agents/scripts/install_extras.py \
    --role design --thin --dry-run
```

The helper sha-verifies each source against `MANIFEST.sha256` before
copying, refuses to overwrite user-customised agents without `--force`,
and merges new entries into `.cursor/pack-manifest.json` so
`verify_integration.py` keeps tracking drift. See
`install_extras.py --help` for the full flag list.

For each specialist copied:
- Define owned modules (which directories it can edit) as a body section.
- List adjacent modules it must NOT edit without approval.
- Add project-specific rules.
- Ensure the frontmatter still passes Schema v2 (see
  `<PACK_DIR>/agents/SCHEMA.md`).

---

**Step 6 — Set up persistent memory**

Copy the whole `<PACK_DIR>/memory/` directory (including
`memory.py`, `validate.py`, `migrations.py`, `SCHEMA.md`, and the three
template files) to `.cursor/memory/`:

```
<PACK_DIR>/memory/*  →  .cursor/memory/
```

Then:

1. **Bootstrap the first state entry** via the CLI — do NOT hand-write it,
   so the correlation_id comes from the enforcement hooks:

   ```
   python3 .cursor/memory/memory.py append \
     --file session-handoff --kind state --status in_progress \
     --summary "Integration bootstrap: <project name>, <stack>" \
     --tags bootstrap,setup \
     --body "## Current State
   - <services running / not started>
   - Tech stack: <langs, frameworks, DBs>

   ## Recent Changes
   - Integrated harmonist

   ## Open Issues
   - <known tech debt>

   ## Deploy Protocol
   - <procedure or TBD>"
   ```

2. **Delete the template placeholder entry** (the one with `id: 0-0-state`)
   from `.cursor/memory/session-handoff.md`. It exists only to make the
   file validate on first integration.

3. **Add memory files to `.gitignore`** unless the project explicitly
   wants to commit them. They will accumulate project-sensitive state.
   Append to `.gitignore`:

   ```
   .cursor/memory/*.md
   !.cursor/memory/*.shared.md
   ```

   Files with the `.shared.md` suffix are the opt-in exception — use them
   when you DO want team-shareable decisions or patterns in git.

---

**Step 7 — Install enforcement hooks + auto-configure bg-regression-runner**

The protocol in `AGENTS.md` is mechanically enforced by Cursor hooks that
block the agent from finishing a code-changing turn until the required
reviewers ran and memory was updated.

Use the pack's upgrade tool to install pack-owned files. It copies
hooks, memory CLI, and the strict orchestration + review agents into
`.cursor/` AND records the current pack version in
`.cursor/pack-version.json` — the anchor for future upgrades.

```
python3 <PACK_DIR>/agents/scripts/upgrade.py --apply
```

This writes:

- `.cursor/hooks.json` (rendered with a Python launcher that exists on this
  host — `python3` / `py -3` / `python`)
- `.cursor/hooks/scripts/hook_runner.py` — the cross-platform active runner
  that `hooks.json` invokes on every OS
- `.cursor/hooks/scripts/{lib,seed-session,record-write,record-subagent-start,record-subagent-stop,gate-stop,gate-shell,git-pre-commit,install-git-hooks}.sh`
- `.cursor/agents/{repo-scout,security-reviewer,code-quality-auditor,qa-verifier,sre-observability,wcag-a11y-gate}.md`
- `.cursor/memory/{memory.py,validate.py,migrations.py,SCHEMA.md,README.md}`
- `.cursor/repomap/repomap.py` — the zero-dependency code-intelligence index
- `.cursor/rules/protocol-enforcement.mdc` — the canonical enforcement rule
- `.cursor/pack-version.json` + `.cursor/pack-manifest.json` (sha256 of every
  pack-owned file, for post-install drift detection)

`bg-regression-runner.md` is seeded automatically by `upgrade.py --apply`:
the script reads your project manifests (`package.json` + lockfile,
`pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`,
`Makefile`, `composer.json`, `mix.exs`, `Gemfile`) and fills in the real
test / lint / typecheck / build commands. If no manifest is found, the
file gets a clearly-marked placeholder block you fill in manually. Once
the file contains real commands (pytest, vitest, cargo test, mvn test,
…) `upgrade.py` stops touching it so hand-edits are preserved.

If you already have a `.cursor/hooks.json` with unrelated hooks, merge
manually: `upgrade.py` will overwrite it.

Preview the detection without applying:

```
python3 <PACK_DIR>/agents/scripts/detect_regression_commands.py
```

Later, when a new version of the pack ships, re-run
`upgrade.py --apply` to refresh those same files without touching
`AGENTS.md`, memory entries, project-domain rules, specialists, or
`bg-regression-runner`.

Verify in Cursor: *Settings → Hooks* — all six hooks (`sessionStart`,
`afterFileEdit`, `subagentStart`, `subagentStop`, `beforeShellExecution`,
`stop`) should load without errors.

---

**Step 8 — Install Cursor Rules**

Create TWO files in `.cursor/rules/`. Hooks provide *mechanical*
enforcement (scripts that can block a response); rules provide
*conversational* reminders inside the prompt window. You need both.

**File 1: `protocol-enforcement.mdc`** — PACK-OWNED, copy verbatim from
the canonical template (already installed by `upgrade.py --apply` in
Step 7). If for some reason it's missing, install it:

```
cp <PACK_DIR>/agents/templates/rules/protocol-enforcement.mdc \
   .cursor/rules/protocol-enforcement.mdc
```

This file carries a `<!-- pack-owned: protocol-enforcement v1 -->`
marker and declares the **precedence chain**: `AGENTS.md` (project
reality) > `protocol-enforcement.mdc` (enforcement: agents, hooks,
memory) > `project-domain-rules.mdc` (domain specifics) > any other
`.mdc` (stylistic). Do NOT edit this file: `upgrade.py --apply` will
refresh it on future pack releases. If you have your own protocol
rules, put them in `project-domain-rules.mdc` instead.

**File 2: `project-domain-rules.mdc`** — PROJECT-OWNED. Start from the
template:

```
cp <PACK_DIR>/agents/templates/rules/project-domain-rules.mdc.template \
   .cursor/rules/project-domain-rules.mdc
```

Then replace the example sections with 5–10 concrete rules specific
to THIS project's domain. Not generic engineering advice — rules
where violation == real bug, security issue, or data corruption.

**Verify there are no conflicts**:

```
python3 <PACK_DIR>/agents/scripts/scan_rules_conflicts.py --project .
```

The scanner refuses rules that would subvert enforcement (e.g.
"skip qa-verifier for hotfixes", "always approve", "disable the
stop hook") and warns on duplicate-purpose files, phantom slug
references, and alwaysApply overload. Exit 1 = fix before continuing.

---

**Step 9 — Automated smoke test**

Do NOT self-smoke-test. Run the dedicated driver:

```
python3 <PACK_DIR>/agents/scripts/smoke_test.py
```

This drives the real enforcement pipeline end-to-end with synthetic
inputs — no LLM in the loop, no trust issues. It exercises two
scenarios:

1. **Happy path**: sessionStart → sentinel write → subagent with
   `AGENT: qa-verifier` marker → memory.py append → stop gate.
   Each step is asserted: state gets created, write is recorded,
   reviewer is credited, memory entry is appended, stop gate allows,
   and `task_seq` advances.
2. **Negative path (gate bites)**: sentinel write only, no reviewer,
   no handoff. The stop hook MUST return `followup_message` — proving
   the enforcement actually engages.

Exit 0 on a clean install, exit 1 on any step failure (with a clear
per-step reason printed), exit 2 when the install is incomplete
(e.g. hooks not copied — points at `upgrade.py --apply`).

`--json` mode emits a structured report for CI pipelines.

Only after this probe passes, start your first real task in a fresh
chat.

---

**Step 10 — Verify everything**

Do NOT self-check. Run the objective verifier from the project root:

```
python3 <PACK_DIR>/agents/scripts/verify_integration.py
```

This is the authoritative gate. The script performs 15 independent
checks (AGENTS.md length + customization, strict agents installed,
specialists picked, bg-regression-runner commands concrete, memory
bootstrapped via CLI, memory schema passes validation, hooks.json
complete, hook scripts executable, rules with `alwaysApply: true`,
domain-rules has ≥ 5 bullets, `.gitignore` protects memory) and exits
non-zero on any error. Every failure comes with a one-line FIX hint.

Rules:

- If `verify_integration.py` exits **non-zero**, go back and fix.
  Self-reporting "I did step X" is not acceptable — the script is the
  truth.
- Re-run until the script exits 0.
- For CI: `verify_integration.py --json` emits machine-readable results
  suitable for pipelines.

If the script reports only `warning`-severity issues (e.g. missing
`.gitignore` entry), it will exit 0 — but fix them before first use.

> **Local telemetry is on by default.** The hooks will start filling
> `.cursor/telemetry/agent-usage.json` from the first session. It's
> `.gitignored` and stays on this machine. After a few weeks of real
> work run
> `python3 <PACK_DIR>/agents/scripts/report_usage.py
> --recommend-removal` to see which installed agents were never invoked
> and can be pruned from `.cursor/agents/` to free context. Disable by
> setting `telemetry_enabled: false` in `.cursor/hooks/config.json`.

---

**Step 11 — Report to user**

Detect the language the user wrote their message in and respond to
them in that language. The report content below is the English
template — translate it into the user's language (preserving the
slugs, filenames, URLs, and numbers verbatim) before printing.

```
Integration complete!

Agents installed: [number]
Domain: [identity from AGENTS.md]
Key invariants: [list]
Domain rules: [list]
Agent index: <PACK_DIR>/agents/index.json (193 agents, 16 categories)

IMPORTANT: Start a NEW CHAT for your first task — rules take full effect in a fresh conversation.

—

Harmonist is built and maintained by GammaLab (https://gammalab.ae).
Track updates, report issues, or contribute:
https://github.com/GammaLabTechnologies/harmonist

Thanks for trusting Harmonist with your project.
```

---

**AFTER INTEGRATION: Start a NEW chat for the first real task.** The rules
take full effect in a fresh conversation.

Follow the AGENTS.md protocol strictly from now on. No exceptions.
