# Enforcement Hooks

> Cursor hooks that turn the AGENTS.md protocol from a prompt-level
> "MANDATORY RULE" into an actual mechanical gate.
> Without them the protocol is a suggestion. With them, the agent **cannot
> finish a code-changing response** until the required reviewers have run
> and memory has been updated.

## Why

`AGENTS.md` declares that every write must be followed by `qa-verifier`
(and further reviewers per trigger table) and that `session-handoff.md`
must be updated at the end of every task. Nothing in the LLM itself
enforces that — it can skip, forget, or hallucinate "already reviewed".

Cursor exposes a hook system that lets us attach real scripts to agent
lifecycle events. These scripts watch what the agent does, keep per-session
state, and at the `stop` event reply with `followup_message` if the
protocol was violated. Cursor then reopens the agent turn so it can finish
the missing steps. A `loop_limit` on the hook caps the number of retries.

## What it enforces

| Trigger | Requirement | Hook |
|---------|------------|------|
| Any file write outside ignored paths | At least one reviewer invoked before `stop` | `gate-stop.sh` |
| Any file write outside ignored paths | `qa-verifier` specifically invoked before `stop` | `gate-stop.sh` |
| Any file write outside ignored paths | `.cursor/memory/session-handoff.md` updated before `stop` | `gate-stop.sh` |
| Session start | Agent is reminded to read `session-handoff.md` and to mark subagent calls | `seed-session.sh` |
| Subagent delegation | Log the call; extract `AGENT: <slug>` marker | `record-subagent-start.sh` |
| Subagent completion | If slug is in the reviewer set, credit it | `record-subagent-stop.sh` |

Explicit opt-out: if the agent's final message contains `PROTOCOL-SKIP: <reason>`,
the gate allows completion and logs the reason. Useful for trivial patches
(typo fixes, comment tweaks) where the full protocol would be theatre.

## The `AGENT: <slug>` contract

Hooks can observe subagent calls but Cursor does not expose "which agent
identity is this subagent running as" — it only knows the subagent *type*
(`generalPurpose`, `explore`, `shell`).

So the pack defines a small contract that makes the subagent identity
machine-readable: the **first line of every subagent prompt must be
`AGENT: <slug>`**, where `<slug>` matches the filename stem in `agents/`.
Example:

```
AGENT: qa-verifier

Verify completeness of the diff in src/api/auth.ts ...
```

`record-subagent-start.sh` parses that marker; `gate-stop.sh` checks the
collected slugs against the configured `reviewer_slugs` to decide whether
the protocol was honored.

## Installation (per project)

When you integrate `harmonist` into a real project, the integration
prompt copies these files into the project's `.cursor/`:

```
<project-root>/
├── .cursor/
│   ├── hooks.json         ← copy of harmonist/hooks/hooks.json
│   └── hooks/
│       ├── scripts/       ← copy of harmonist/hooks/scripts/
│       └── config.json    ← optional project override (see below)
```

Cursor reloads `hooks.json` automatically on save. Verify in Cursor's
*Settings → Hooks* tab that all five hooks loaded without errors.

## Configuration

Default policy is baked into `scripts/lib.sh`. Override per project by
creating `.cursor/hooks/config.json`:

```json
{
  "require_qa_verifier": true,
  "require_any_reviewer": true,
  "require_session_handoff_update": true,
  "required_reviewer_slug": "qa-verifier",
  "reviewer_slugs": [
    "qa-verifier",
    "security-reviewer",
    "code-quality-auditor",
    "sre-observability",
    "bg-regression-runner"
  ],
  "skip_path_patterns": [
    "^\\.cursor/", "^\\.git/", "^node_modules/", "^\\.venv/",
    "^dist/", "^build/", "^target/", "^coverage/"
  ],
  "memory_paths": [
    ".cursor/memory/session-handoff.md",
    ".cursor/memory/decisions.md",
    ".cursor/memory/patterns.md"
  ]
}
```

Anything you omit falls through to the defaults.

## Debugging

- Per-session state: `.cursor/hooks/.state/session.json`
- Activity log: `.cursor/hooks/.state/activity.log`
- Cursor UI: *Settings → Hooks* shows load status and recent invocations.

If the gate is blocking when it shouldn't: inspect `session.json` — it
tells you exactly which writes, reviewers, and memory updates Cursor has
seen in this session. If a reviewer ran but wasn't credited, it almost
always means the `AGENT: <slug>` marker was missing from the subagent
prompt; tell the orchestrator to re-delegate with the marker.

## Tests

Pure integration tests (no Cursor required) simulate hook invocations by
piping synthetic JSON into each script and checking state / output:

```bash
bash hooks/tests/run-hook-tests.sh
```

Covers the 8 canonical scenarios (pure Q&A, bare write, full-protocol run,
missing qa-verifier, PROTOCOL-SKIP opt-out, ignored paths, missing AGENT
marker, alternative field names).
