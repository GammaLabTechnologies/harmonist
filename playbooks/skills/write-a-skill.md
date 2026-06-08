# Write a Skill

**Use when:** authoring a new skill for `playbooks/skills/` — when a recurring
task keeps getting re-improvised and deserves a written method.

## Method

1. **Find the trigger.** Name the recurring task and the *one line* that
   describes when an agent should reach for this skill. If you can't state the
   trigger crisply, the skill is too broad — split it.
2. **Draft the shape.** Skills here are plain markdown (no agent frontmatter):
   ```
   # <Skill name>
   **Use when:** <one-line trigger>
   ## Method      (numbered, concrete steps)
   ## Output      (what the agent must return)
   ## Guardrails  (limits, failure modes, when NOT to use it)
   ```
3. **Keep it tight.** Aim for under ~100 lines. If it grows past that or mixes
   distinct domains, move depth into a sibling `REFERENCE.md` and link to it
   (progressive disclosure — the agent loads detail only when needed).
4. **Write for an agent, not a human reader.** Be imperative and concrete.
   Prefer checklists and exact commands over prose. No time-sensitive facts.
5. **Decide on scripts.** If the skill needs a deterministic operation
   (validation, formatting, parsing), ship a small stdlib script next to it
   rather than describing code the agent must regenerate each time.
6. **Register it.** Add a row to `playbooks/skills/README.md` and, if a
   specific agent should own it, mention it in that agent's body.

## Output

- the new `playbooks/skills/<slug>.md` following the shape above
- a README table row (slug + the one-line "use when")
- (optional) a sibling `REFERENCE.md` / script if the method needs it

## Guardrails

- The trigger line is the most important sentence — it's how the orchestrator
  decides to load the skill. Make it specific (keywords, context), not "helps
  with X".
- One skill = one job. Resist bundling.
- Skills are content (not agents): not linted, indexed, or in the supply-chain
  manifest. Keep them self-contained and dependency-free.
