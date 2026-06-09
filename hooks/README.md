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
| Subagent delegation | DENY the launch if `max_concurrent_subagents` are already running (prevents RAM-exhausting fan-out) | `record-subagent-start.sh` |
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
*Settings → Hooks* tab that all six hooks loaded without errors
(`sessionStart`, `afterFileEdit`, `subagentStart`, `subagentStop`,
`beforeShellExecution`, `stop`).

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
  ],
  "max_concurrent_subagents": 3,
  "subagent_stale_seconds": 900,
  "require_affected_tests": false,
  "repomap_staleness_warn": true,
  "require_delegation_context": false,
  "min_delegation_chars": 80,
  "hitl_enabled": true,
  "dangerous_command_action": "ask"
}
```

Anything you omit falls through to the defaults.

### Impact-aware gate (repo map)

When `require_affected_tests` is `true`, the `stop` hook uses the local
repo map (`.cursor/repomap/`) to compute the **test files affected** by the
task's edits, and refuses to finish until a regression run has passed
(`last_regression_ok`) when affected tests exist — so a change can't ship
without the tests it can actually break being run. Off by default (opt in per
project, like `require_regression_passed`). With `repomap_staleness_warn`
(default `true`), `sessionStart` prints a one-line banner when the map is
stale or unbuilt. Both are no-ops if the repo map isn't installed.

### Concurrency cap (memory safety)

Unbounded parallel subagent fan-out (mesh topology) can spawn many
heavyweight subagents at once and exhaust RAM — a real risk now that
agents run on a 1M-context model. The `subagentStart` hook enforces a
hard cap:

- **`max_concurrent_subagents`** (default `3`) — once this many subagents
  are running (started, not yet stopped) in the current task, the next
  `subagentStart` returns `{"permission": "deny"}` and the launch is
  blocked. The orchestrator is told to wait for an active subagent to
  finish, then dispatch the next. Set to `0` to disable the cap entirely.
- **`subagent_stale_seconds`** (default `900`) — a subagent whose start is
  older than this with no observed `subagentStop` is treated as finished
  for the count, so a missed stop event can never permanently lock out new
  launches.

Denials are counted in telemetry under `summaries.subagent_cap_denials`.

### Delegation-context gate (opt-in)

A subagent only sees the prompt you pass it — not your conversation. A
marker-only / contextless delegation makes it guess and redo work. With
**`require_delegation_context`** on, `subagentStart` DENIES a `task` whose
handoff (prompt minus the `AGENT:` marker) is shorter than
**`min_delegation_chars`** (default 80), forcing a real handoff package
(target/scope, the single sub-goal, constraints, success criteria). Off by
default; counted under `summaries.delegation_context_denials`.

### HITL gate on dangerous commands

The **`beforeShellExecution`** hook is human-in-the-loop protection: it matches
the command against **`dangerous_command_patterns`** (root/home/wildcard
deletes, `git push --force`, `dd`/`mkfs`, fork bombs, `curl … | sh`,
`DROP/TRUNCATE`, …) and returns **`ask`** (the human confirms) or **`deny`**,
per **`dangerous_command_action`**. On by default (`hitl_enabled: true`,
action `ask`) and tuned to leave routine commands (e.g. `rm -rf node_modules`)
alone — only catastrophic ones pause. Counted under `summaries.hitl_gated`.
This phase is handled by the Python hook runner (the active path on every OS).

## Limitations & operational notes

Read this section before relying on the gate in anger.

### One project, one Cursor window

All hooks share a single per-project state file
(`.cursor/hooks/.state/session.json`). If you open the **same project in
two Cursor windows**, both write to that file, and the second window's
`sessionStart` **resets it** — clobbering the first window's in-flight
task state (recorded writes, credited reviewers, correlation id). The
gate's verdicts then become unpredictable for both windows: a task can
be blocked for steps it already did, or allowed because its writes were
wiped. Recommendation: keep enforced work in **one window per project**.
Concurrent hook *invocations within one window* are fine — they are
serialized by an advisory lock on `session.json.lock` (with a ~10s
timeout; on timeout the hook logs a warning to `activity.log` and
proceeds unlocked rather than freezing Cursor).

### `loop_limit` lives in TWO files — keep them in sync manually

- `hooks.json` → `loop_limit` on the `stop` hook is **Cursor's** retry cap.
- `config.json` → `loop_limit` is what the **gate itself** uses to decide
  when to fail-closed (`emit_exhausted`, incident record, task bump).

Nothing synchronizes them. If `hooks.json` allows more retries than
`config.json`, the gate exhausts early; if fewer, Cursor stops retrying
before the gate's fail-closed branch can fire and the last followup is
simply surfaced to the user. When you change one, change the other.

### python3 ≥ 3.9 is required

Every hook (including the `.sh` reference path, whose helpers shell out
to Python) needs `python3` on PATH:

- **python3 present but < 3.9**: most scripts exit immediately with code 3
  and a clear upgrade message (the `PY-GUARD` preamble). `hook_runner.py`
  is the exception: its guard still answers Cursor in-protocol (exit 0)
  so the enforcement layer degrades **loudly** instead of vanishing —
  recorder phases emit `{}`, `beforeShellExecution` returns
  `{"permission": "ask"}` naming the python requirement, and `stop`
  returns a `followup_message` stating the protocol gate cannot be
  verified until python3 is upgraded.
- **python3 missing entirely**: the hook command fails to start, Cursor
  receives no JSON, and treats the event as un-hooked — recorder phases
  record nothing and the stop gate never fires. **Enforcement silently
  degrades to OFF.** The only exception is the POSIX `gate-shell.sh`,
  which asks for human confirmation when it cannot evaluate a command.
  Verify in *Settings → Hooks* that all six hooks loaded without errors
  after installation.

### The gates are process nudges, not a security boundary

`.cursor/` state and config (`hooks/.state/session.json`, `hooks/config.json`)
match the default `skip_path_patterns`, so an agent's edit tool can rewrite
them without the write being recorded; and `PROTOCOL-SKIP` is honor-system —
logged and telemetry-audited (sessionStart surfaces an abuse banner), never
blocked. The gates keep honest-but-forgetful agents on protocol; they are
not tamper-proof against a deliberately adversarial one.

### Per-event interpreter spawn cost

Every hook event — including **every single shell command** via
`beforeShellExecution` — spawns a fresh `python3` process (typically
~30–100 ms; more on cold AV-scanned Windows machines or network
filesystems). For shell-heavy sessions this adds noticeable latency per
command. Setting `hitl_enabled: false` skips the pattern evaluation but
still spawns the interpreter; to remove the cost entirely, delete the
`beforeShellExecution` entry from `hooks.json` (you lose the HITL gate).

## Debugging

- Per-session state: `.cursor/hooks/.state/session.json`
- Activity log: `.cursor/hooks/.state/activity.log` (auto-capped at ~1 MiB —
  the older half is discarded on rotation)
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
