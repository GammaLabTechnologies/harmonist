# Templates & Workflow Examples

This directory holds two things:

1. **Blank agent templates** — Schema v2-compliant starter files you can copy
   into `.cursor/agents/` and fill in for a specific project.
2. **Workflow examples** — narrative markdown showing how multiple agents from
   the unified pool collaborate on a real mission. Not schema-bound.

---

## Agent templates

| Template | Use as starting point for |
|----------|--------------------------|
| [`backend-engineer.md`](./backend-engineer.md) | Any backend write agent (controllers, services, data layer) |
| [`frontend-engineer.md`](./frontend-engineer.md) | Any frontend write agent (React/Vue/Svelte, mobile webviews) |
| [`infra-engineer.md`](./infra-engineer.md) | Any infrastructure write agent (Docker, CI/CD, migrations) |

All three follow Schema v2 (see [`../SCHEMA.md`](../SCHEMA.md)) and include
`<!-- CUSTOMIZE: … -->` markers for the bits you must fill in per project.
Pick one, drop it in `.cursor/agents/`, rename the slug, and tailor the
`Your Scope` / `Tech Context` / `Rules` blocks to your codebase.

Once filled in, either keep it project-local (in `.cursor/agents/`) or land it
as a proper category agent under `agents/<category>/` and run
`python3 ../scripts/build_index.py` to pick it up.

---

## Workflow examples

These files are read-only docs showing how the agency collaborates. Not
agents, no frontmatter.

| File | Describes |
|------|-----------|
| [`nexus-spatial-discovery.md`](./nexus-spatial-discovery.md) | 8 agents deployed in parallel to evaluate a software opportunity and produce a unified product blueprint. |
| [`workflow-book-chapter.md`](./workflow-book-chapter.md) | Single-agent workflow for turning raw source material into a strategic first-person chapter draft. |
| [`workflow-landing-page.md`](./workflow-landing-page.md) | 4-agent landing-page sprint — copy, design, build, QA in one day. |
| [`workflow-startup-mvp.md`](./workflow-startup-mvp.md) | End-to-end MVP bootstrap — discovery, architecture, build, launch. |
| [`workflow-with-memory.md`](./workflow-with-memory.md) | How the memory files (`session-handoff`, `decisions`, `patterns`) stitch across sessions. |

---

## Adding new templates / examples

- **New agent template**: follow `SCHEMA.md`, include CUSTOMIZE markers,
  make sure `name` is a unique slug, and link it from the table above.
- **New workflow example**: plain markdown, no frontmatter. Show which
  agents (by slug) participated, in what order, and what artifacts each one
  produced. If you reference `index.json` or specific tags in the narrative,
  the example will keep working even when the underlying agent names change.
