# Skills — reusable task playbooks

A **skill** is a small, self-contained playbook for a recurring task: the
step-by-step method an agent should follow, plus what it must return. Unlike
the NEXUS phase playbooks (which structure a whole project lifecycle) and
unlike agents (which are *who* does the work), a skill is *how* to do one
specific job well — loaded on demand when a task matches it.

## When to use a skill

The orchestrator (or a persona agent) pulls in the relevant skill when a task
matches its trigger, then follows the method instead of improvising. Skills
keep recurring procedures consistent across sessions and agents.

## Format

Each `*.md` here is a plain playbook (no frontmatter schema — these are not
agents). Keep them to a tight shape:

```
# <Skill name>
**Use when:** <one line — the trigger>
## Method
1. ...
## Output
- ...
## Guardrails
- ...
```

## Available skills

| Skill | Use when |
|-------|----------|
| `secure-code-review.md` | Reviewing a diff/PR for security defects before merge |
| `authorized-web-pentest.md` | Running a sanctioned web-app security test end to end |
| `incident-response.md` | Triaging and resolving a production incident |
| `dependency-vulnerability-remediation.md` | A CVE / advisory lands on a dependency you ship |
| `security-privacy-hardening-checklist.md` | Hardening or reviewing a feature/release against a security + privacy baseline |
| `to-prd.md` | Turning a one-line idea/request into a concrete, buildable PRD |
| `brainstorming.md` | Generating + comparing options for an open-ended decision |
| `write-a-skill.md` | Authoring a new skill for this library |

A structured, schema-validated checklist backs the last skill at
`playbooks/checklists/` (`security-privacy-hardening.json` + `schema.json` +
`validate.py`).

## Adding a skill

Drop a new `*.md` here following the shape above, add a row to the table, and
reference it from the agent(s) or playbook(s) that should reach for it. Skills
are content — they are not linted or indexed like agents, and they are not
part of the supply-chain manifest.
