# Turn an Idea into a PRD

**Use when:** a feature request or idea arrives as a one-liner and needs to
become a concrete, buildable product requirements doc before work starts.

## Method

1. **Pin the problem, not the solution.** State who has the problem, what it
   costs them today, and how you'll know it's solved (a measurable outcome).
   If the request is phrased as a solution, recover the underlying problem.
2. **Scope crisply.** List what's IN, what's explicitly OUT (v1 vs later), and
   the assumptions/constraints (platform, deadline, dependencies). A PRD with
   no "out of scope" section will sprawl.
3. **Write user stories with acceptance criteria.** For each story:
   "As a <user>, I want <capability> so that <outcome>" + concrete,
   testable acceptance criteria (the cases QA will check).
4. **Surface the unknowns.** List open questions and risks; flag the ones that
   block estimation. Don't paper over ambiguity — name it.
5. **Define done.** Success metrics, rollout plan, and what telemetry proves
   the outcome was achieved.

## Output

- problem_statement: who / pain / measurable success
- scope: in / out (v1 vs later) / assumptions / constraints
- user_stories: each with acceptance criteria
- non_functional: performance, security, privacy, accessibility expectations
- open_questions and risks (blockers flagged)
- success_metrics and rollout plan

## Guardrails

- A PRD describes *what* and *why*, not *how* — leave implementation to the
  engineers/architects (route to them after).
- Acceptance criteria must be testable; "works well" is not a criterion.
- Keep v1 small. Move everything non-essential to a "later" list.
