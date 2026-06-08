# Security & Privacy Hardening Checklist

**Use when:** hardening a feature, service, or release — or reviewing one —
against a consistent baseline of application security and privacy controls.

The checklist itself is structured data at
`playbooks/checklists/security-privacy-hardening.json` (sections × items, each
tagged `essential` / `recommended` / `advanced`), validated by
`playbooks/checklists/validate.py`. This skill is how to *apply* it.

## Method

1. **Load the checklist** and scope it to what you're hardening. Read the
   section `intro`s; pull the items relevant to this change (e.g. an auth
   feature → Authentication & Sessions + Authorization + Logging).
2. **Walk `essential` first.** For each essential item, determine: met /
   not-met / not-applicable — with evidence (a file:line, a config, a test),
   not an assertion. `essential` gaps are release blockers.
3. **Then `recommended`, then `advanced`** as scope and risk justify. Note
   accepted risk explicitly rather than silently skipping.
4. **Privacy items are first-class.** The Data Protection & Privacy section is
   not optional polish — for anything touching personal data, treat its
   `essential` items like any other blocker. Delegate deeper work to the
   `privacy-engineer` agent.
5. **Hand off / record.** Feed gaps to the relevant agent (e.g.
   `security-web-app-pentester` to confirm an injection risk, `privacy-engineer`
   for a data-minimization fix) and record decisions/accepted-risk in
   `decisions.md`.

## Output

- coverage: per relevant item — met | not-met | n/a, with evidence
- blockers: the `essential` gaps that must be closed before shipping
- recommendations: `recommended`/`advanced` items worth doing, prioritized
- accepted_risk: anything deliberately skipped, with rationale and an owner

## Guardrails

- Evidence over checkboxes — "met" needs a concrete reason a reviewer can check.
- The checklist is a baseline, not a ceiling: project `AGENTS.md` Invariants
  win, and a real threat the list doesn't cover still matters.
- Keep the JSON valid (`validate.py`) when you extend the checklist.
