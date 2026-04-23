# Agent Style Guide

> How to write an agent body that is useful, compact, and consistent with
> the rest of the pack. Follow this when adding a new agent or materially
> rewriting an existing one.
>
> This is a *companion* to `SCHEMA.md`. SCHEMA defines the **shape** every
> file must conform to (frontmatter, category, tags, slug). STYLE defines
> what the **body** should read like.

---

## 1. The two canonical shapes

There are exactly two shapes in this pack. Pick one and stay in it.

### A. Strict shape (reviewers, scouts, orchestrators)

Short, reference-style, deterministic. Target: 40–80 non-blank body lines.

```markdown
You are <role>. <One-sentence identity — what perspective you argue from>.

Your task:
1. <concrete, observable step>
2. <concrete, observable step>
3. ...

## <One or two domain checklists>
For every <input unit>, verify:
- <concrete check>
- <concrete check>

Do not <common anti-pattern>.

Return exactly:
- <field>
- <field>
- <field>
```

Templates in this family: `agents/review/qa-verifier.md`,
`agents/review/security-reviewer.md`, `agents/review/bg-regression-runner.md`,
`agents/orchestration/repo-scout.md`.

These agents participate in the orchestration contract and produce
**structured output** the parent can parse. No flavour, no memory
metaphors, no "I remember…" prose. They are machines.

### B. Persona shape (specialists, write agents, coaches)

Longer, opinionated, domain-dense. Target: 80 non-blank lines of essentials
plus any amount of deep-reference material cut off by `## Deep Reference`.

```markdown
# <Display Name>

<!-- precedence: project-agents-md -->
> Project `AGENTS.md` (Invariants / Platform Stack / Modules) overrides
> any advice in this persona. When they conflict, follow the project
> rules and surface the conflict explicitly in your response.

You are **<Display Name>**, <one-sentence identity>.

## Core Mission

<2–4 sentences or bullets about what this agent exists to do. Concrete.
No "strategic", "world-class", "best-in-class" adjectives — describe the
deliverable, not the attitude.>

## Critical Rules

1. <Non-negotiable constraint, ideally one the domain frequently violates.>
2. <Rule that would prevent a common failure mode.>
3. <Rule that forces honesty: "if you didn't test X, say so instead of
   asserting it works".>
…

## Output / Deliverables

<What every typical invocation produces. Tables, templates, or a
structured report shape. Short. Concrete fields, not vibes.>

## Deep Reference

<Everything below is reference material: long templates, frameworks,
edge-case playbooks, scenario walkthroughs. The `--thin` converter cuts
here, so anything below this marker is NOT loaded on every invocation.>

## <Framework X>
…

## <Scenario: Y>
…
```

Use this shape for every persona agent.

---

## 2. What NOT to write

The audit pass that produced this guide found a recurring set of
anti-patterns. Avoid all of them. If you're rewriting an old agent, these
are the first things to cut.

### 2.1 Personality theatre

```text
## 🧠 Your Identity & Memory
- **Personality**: Strategic, security-focused, scalability-minded,
  reliability-obsessed
- **Memory**: You remember successful architecture patterns, performance
  optimizations, and security frameworks
- **Experience**: You've seen systems succeed through proper architecture
  and fail through technical shortcuts
```

Zero signal. The model does not "remember" anything between invocations —
memory lives in `.cursor/memory/`, not in a bulleted vibe block. Drop
the whole section. Open with a single identity line: "You are Backend
Architect — a senior backend architect focused on scalable systems and
data integrity on high-traffic production platforms."

### 2.2 Adjective soup

"World-class", "senior", "expert-level", "best-in-class", "strategic",
"experienced". These are free to type and free to ignore. Replace with a
concrete constraint: "designs for ≥10k writes/s", "reviews every diff
against OWASP Top 10", "requires evidence from logs / metrics / tests
before signing off".

### 2.3 Emoji-prefixed section headings

`## 🧠 Your Identity & Memory`, `## 🎯 Your Core Mission`,
`## 🚨 Critical Rules You Must Follow`. These make the body harder to
parse for extractors, trip up `--thin` heuristics, and survive several
encoding round-trips as mojibake (`=Ë ` instead of `📋`).

Use plain ASCII headings: `## Core Mission`, `## Critical Rules`,
`## Output`, `## Deep Reference`. If you want a visual marker, put it
in a prose sentence, not in the heading.

### 2.4 "Your Success Metrics" / "Advanced Capabilities"

Aspirational sections that describe what "success" would look like if
the agent existed as a person. The agent doesn't have a career. The
model doesn't optimise against these sentences — they cost tokens and
add nothing. Cut them or fold the one useful bullet into Critical Rules.

### 2.5 Cross-Agent Collaboration lists

```text
- **Evidence Collector**: Provide accessibility-specific test cases …
- **Reality Checker**: Supply accessibility evidence …
- **Frontend Developer**: Review component implementations …
```

Routing is done by the **orchestrator** via `index.json` / tags — the
agent body is not where cross-agent hand-offs are declared. If two
agents genuinely chain, that belongs in `distinguishes_from` +
`disambiguation` (machine-readable) or in a playbook under
`playbooks/runbooks/`, not in persona prose.

### 2.6 Re-stating the Precedence block

Every persona agent now carries the `<!-- precedence: project-agents-md -->`
header. That's enough. Don't re-paraphrase it in Critical Rules,
Workflow, or Communication Style.

---

## 3. What to include

### 3.1 One identity line

"You are <Role>, <the lens through which you argue>". That's it. The
rest of the body is the job, not the personality.

### 3.2 A Mission that is disprovable

A mission sentence is useful only if you can read a diff and say
"this did/did not satisfy it". "Ensure system reliability" fails this
test. "Every write path has an idempotency key, a retry policy, and a
compensating action" passes.

### 3.3 Rules that close a known failure mode

Each Critical Rule should correspond to a real incident pattern in that
domain. "Don't use floats for money" is valid because it's the #1 cause
of off-by-penny accounting bugs. "Always write clean code" is not,
because it fails nowhere and everywhere.

A good rule has this shape:

> **Rule**: <what to do or never do>
> **Why**: <the failure this prevents, stated concretely>

Keep 3–8 rules. More than 10 means the agent is doing two jobs — split.

### 3.4 A structured Output / Deliverables contract

Strict agents list literal fields ("Return exactly: verdict, findings,
…"). Persona agents can use a template. Either way, something in every
response should be mechanically extractable.

### 3.5 `## Deep Reference` for anything over 80 non-blank body lines

If the body is growing with templates, scenario walkthroughs, code
snippets — put the marker at the natural boundary (usually right after
Critical Rules / Output, before Technical Deliverables or Workflow).
The `--thin` converter reads the marker explicitly; the budget-based
fallback is fuzzier and can cut in the wrong place.

`scripts/insert_deep_ref_marker.py` will do this automatically for the
common persona shape. Re-run it after growing an agent body.

---

## 4. The lightweight persona template

Copy-paste starting point. Under 80 lines total; everything below the
marker is optional.

```markdown
---
schema_version: 2
name: <Display Name>
description: <One-sentence description for routing (≤ 240 chars).>
category: <category>
protocol: persona
readonly: false
is_background: false
model: inherit
tags: [<3–8 tags from agents/tags.json>]
domains: [all]
version: 1.0.0
updated_at: YYYY-MM-DD
vibe: <short tagline, ≤ 140 chars>
---

# <Display Name>

<!-- precedence: project-agents-md -->
> Project `AGENTS.md` (Invariants / Platform Stack / Modules) overrides
> any advice in this persona. When they conflict, follow the project
> rules and surface the conflict explicitly in your response.

You are **<Display Name>**, <one-sentence identity>.

## Core Mission

<Disprovable mission: 2–4 bullets about what a successful invocation
produces. Concrete, not aspirational.>

## Critical Rules

1. **<Rule>** — <Failure mode this prevents.>
2. **<Rule>** — <Failure mode this prevents.>
3. **<Rule>** — <Failure mode this prevents.>
4. **Be honest about what you didn't verify.** Say "not tested" instead
   of asserting something you haven't checked.

## Output

Every response ends with:

- <field>: <what goes here>
- <field>: <what goes here>
- <field>: <what goes here>

## Deep Reference

<Templates, scenarios, long checklists, code snippets. Everything below
this line is excluded from `--thin` integrations and only pulled in when
the agent determines it needs the detail.>
```

---

## 5. Retrofit checklist

Cleaning up an existing persona? Run the body through this list.

- [ ] No `## 🧠 Your Identity & Memory` block with Personality / Memory /
      Experience bullets.
- [ ] No adjective soup in Core Mission ("world-class", "strategic",
      "best-in-class"…).
- [ ] Emoji removed from H2 headings; prose-level emoji kept only when
      it carries information.
- [ ] No "Your Success Metrics" section (or it's been merged into
      Critical Rules).
- [ ] No "Cross-Agent Collaboration" list (hand-offs go through
      `distinguishes_from` + orchestrator routing).
- [ ] No re-paraphrasing of the precedence header.
- [ ] Body has a `## Deep Reference` marker if it exceeds ~80 non-blank
      lines.
- [ ] Frontmatter has `version`, `updated_at`, and bumped them as part
      of the edit.
- [ ] `python3 agents/scripts/lint_agents.py <file>` passes with zero
      warnings.

---

## 6. When the pack and the style guide disagree

The pack currently ships 179 persona agents that predate this guide
and were imported from upstream catalogs. Most of them violate §2.1–2.5
in some way. That's a known backlog — do not "fix in passing" all
agents in one commit. Instead:

- **New agents**: follow STYLE.md from day one.
- **Existing agents**: bring them into compliance when you're *already*
  editing them for content (new capability, updated best practice,
  deprecation, etc.). Use the §5 checklist. Bump `version` and
  `updated_at` as part of the commit.
- **Bulk retrofits**: open a dedicated PR, one category at a time, with
  the before/after lint output in the description.

Drive-by emoji cleanup is noise. Content-driven cleanup is signal.
