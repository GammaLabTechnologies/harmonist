> **If you are an AI agent asked to install or integrate this pack:** read
> [`integration-prompt.md`](integration-prompt.md) and execute its steps.
> Do not apply [`AGENTS.template.md`](AGENTS.template.md) as a live rule inside
> the pack folder — it is the template that becomes the user project's
> `AGENTS.md` during integration.

<div align="center">

# Harmonist

### Portable AI agent orchestration with mechanical protocol enforcement

*A drop-in multi-agent framework for Cursor, Claude Code, Copilot, Windsurf, Aider, and other AI coding assistants.*

[![GitHub stars](https://img.shields.io/github/stars/GammaLabTechnologies/harmonist?style=flat&logo=github&color=yellow)](https://github.com/GammaLabTechnologies/harmonist/stargazers)
[![CI](https://github.com/GammaLabTechnologies/harmonist/actions/workflows/ci.yml/badge.svg)](https://github.com/GammaLabTechnologies/harmonist/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Version 1.2.3](https://img.shields.io/badge/version-1.2.3-brightgreen.svg)](CHANGELOG.md)
[![Agents: 193](https://img.shields.io/badge/agents-193-purple.svg)](agents/index.json)
[![Tests: 550+](https://img.shields.io/badge/tests-550+-success.svg)](#testing)
[![Stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-lightgrey.svg)](#requirements)

**Built and maintained by [GammaLab](https://gammalab.ae) · [@GammaLabTechnologies](https://github.com/GammaLabTechnologies)**

</div>

---

Most AI coding frameworks trust the language model to follow the
rules. Harmonist **refuses to let it skip them**. Every code-changing
turn is gated by hooks that check whether the required reviewers ran,
whether memory was updated, and whether the supply chain of every
shipped file is intact. If the checks fail, the turn doesn't complete —
no matter how confidently the model claims it's done.

This is the first open-source agent framework where **protocol
enforcement is a mechanical gate, not a polite request in a prompt**.

---

## Table of contents

- [Why Harmonist exists](#why-harmonist-exists)
- [What makes it different](#what-makes-it-different)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [The 193-agent catalogue](#the-193-agent-catalogue)
- [Mechanical enforcement](#mechanical-enforcement)
- [Structured validated memory](#structured-validated-memory)
- [Supply-chain integrity](#supply-chain-integrity)
- [Supported IDE integrations](#supported-ide-integrations)
- [Key scripts](#key-scripts)
- [Documentation](#documentation)
- [Testing](#testing)
- [FAQ](#faq)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [About GammaLab](#about-gammalab)

---

## Why Harmonist exists

AI coding assistants have a structural problem that prompt engineering
alone cannot fix.

**The problem:** every serious engineering workflow has non-negotiable
rules — "no floating-point for money", "run QA before merging", "every
external call retries with idempotency keys", "security review before
touching auth code". An LLM can be *told* to follow them, but there is
no mechanism forcing it to. It can agree, move on, and silently skip
the step. On a good day you notice. On a bad day the bug ships.

**The current landscape** is split between two imperfect answers:

- **Thin agent frameworks** (LangChain, CrewAI, AutoGen, MetaGPT and
  many others) give you orchestration primitives but leave enforcement
  to the prompt. The model can always override its own protocol.
- **Heavy enterprise platforms** promise governance through separate
  runtimes, databases, and vendor lock-in — but need infrastructure to
  install, don't work on a solo developer's laptop, and can't be
  audited file-by-file.

**Harmonist takes a different stance.** Protocol enforcement is
implemented as IDE-level hooks — concrete shell and Python scripts that
observe every subagent dispatch, every file edit, every session stop.
When the rules the project declared aren't met, the `stop` hook
returns a `followup_message` to the AI and refuses to allow the turn
to complete. The model can't argue with that; it's a state machine on
disk.

No runtime. No database. No vendor lock-in. Just markdown, stdlib
Python, and bash — sitting next to your code, doing one job
correctly.

---

## What makes it different

Seven concrete, checkable properties — each one addresses a gap that
other open-source agent frameworks leave open.

### 1. Mechanical protocol enforcement via IDE hooks

The `stop` hook in `.cursor/hooks/` parses subagent dispatch markers
from the session, checks whether `qa-verifier` ran, whether any
required reviewer was missing, whether `session-handoff.md` was
updated, and returns a structured `followup_message` if the turn
is incomplete. `loop_limit: 3` caps retries. On exhaustion, an
incident is recorded and surfaced in the next session. **The AI
literally cannot ship a code change that skipped review.**

### 2. Supply-chain verification of agent definitions

All runtime-shipped content — `agents/`, `hooks/`, `memory/`,
`playbooks/`, root docs — is hashed in `MANIFEST.sha256` (CI configs
and repo metadata are pack-repo-only and excluded). `upgrade.py`
sha-verifies each source *before* copying into a project. A tampered
`security-reviewer.md` (say, one that returns `approve` for
everything) is REFUSED — it never enters the project. `install_extras.py`
inherits the same guard for on-demand specialist installs. This is
the first OSS agent catalogue with paranoid-level supply-chain
posture.

### 3. Memory correlation IDs that the LLM cannot forge

Every memory entry has a `correlation_id` of the form
`<session_id>-<task_seq>` generated by the hooks at session start
(`<unix-seconds><pid4>` — collision-safe across parallel sessions).
The LLM reads the active ID via CLI; it never writes the ID itself.
This means the linkage between a `state` entry, a `decision`, and a
`pattern` from the same task is **cryptographically ordered** from
the hook's perspective — not trusted to the model.

### 4. Schema-validated memory with secret-pattern scanning

`memory.py append` is the only supported write path. It validates
every entry against a YAML schema (`memory/SCHEMA.md`), rejects
duplicates, and scans the body for ~30 classes of secrets: AWS
access keys, GitHub PATs, Stripe tokens, Slack webhooks, GCP service
accounts, Azure connection strings, Telegram bot tokens, Discord
tokens, Heroku/Postmark UUIDs (context-scoped), generic high-entropy
tokens with `secret:` prefixes, and DB connection strings with
embedded credentials. Placeholder fences (`${VAR}`, `<NAME>`) suppress
the scan so your templates still write cleanly.

### 5. 193 curated domain specialists, not one generic "coder"

Harmonist's catalogue is not a handful of roles. It's **193 curated
specialists** across 16 categories: blockchain-security-auditor for
Solidity audits, zk-steward for zero-knowledge circuits, visionos-
spatial-engineer for Apple Vision Pro, wechat-mini-program-developer
and xiaohongshu-specialist for the China market, laravel-livewire-
specialist for PHP, roblox-systems-scripter for Roblox Luau, 30+
marketing agents from SEO to Douyin, finance / sales / product /
support / academic coverage. The orchestrator picks by
`domains × roles × tags`, not by hard-coded slug lists.

### 6. Integration-as-a-prompt

There is no installer binary. Integration happens by pasting
`integration-prompt.md` into a Cursor Agent-mode session. The AI
reads the prompt, analyzes the project, asks the user which `roles`
will be active (engineering / design / product / marketing / sales /
support / finance / testing / academic), selects the right
specialists from `agents/index.json`, and wires everything up —
including writing a project-specific `AGENTS.md` with domain-tailored
invariants. The AI integrates itself.

### 7. Zero runtime dependencies, runs natively on every OS

No npm, no Docker, no LangChain, no vector database. **Pure Python
stdlib** (with optional POSIX `.sh` conveniences). Integration,
upgrade, conversion, install, and the enforcement runtime all run
natively on **Windows, macOS, and Linux** — no WSL or Git Bash
required. The enforcement runtime has two implementations — POSIX
`.sh` scripts for macOS / Linux / WSL, and a pure-Python
`hook_runner.py` that is the active path on every OS (and the only
one on native Windows). `upgrade.py` renders `.cursor/hooks.json`
with a Python launcher that actually exists on the host (`py -3` /
`python` on Windows, `python3` on POSIX). `.gitattributes` pins
`eol=lf` so a Windows checkout can't break `MANIFEST.sha256`. Both
hook paths are exercised against identical scenarios, and a
native-Windows CI job runs the full install path end-to-end.

---

## Requirements

- **Python 3.9+** — every script ships with a version guard; older
  interpreters exit with a per-OS install hint.
- **Bash 3.2+** is *optional* — only needed for the POSIX `.sh`
  conveniences and the shell test harness (macOS default works). All
  integration, upgrade, conversion, and install tooling is pure Python,
  and on native Windows the pure-Python `hook_runner.py` is the active
  hook path; no WSL or Git Bash required.
- **Git** for version tracking.
- **An AI coding assistant that supports subagent dispatch** — Cursor
  is the primary integration; Claude Code, Copilot, Windsurf, Aider,
  Kimi, Qwen, Gemini CLI, OpenCode, OpenClaw, and Antigravity are all
  supported via adapters.
- **No third-party Python dependencies** — stdlib only. No npm, no
  Docker, no LangChain, no vector database.

---

## Quick start

> The pack folder may have any name — docs refer to it as `<PACK_DIR>`.
> Examples below assume the folder is named `harmonist` (what `git clone`
> produces). The protocol template ships as
> [`AGENTS.template.md`](AGENTS.template.md); the file generated in YOUR
> project during integration is named `AGENTS.md`.

### Option 1 — Integrate via Cursor (recommended)

```bash
# 1. Clone into the root of your project (keep it as a SUBFOLDER —
#    do not unpack the pack's files into your project root)
cd your-project/
git clone https://github.com/GammaLabTechnologies/harmonist.git

# 2. Open the project in Cursor, switch to Agent mode
# 3. Paste the contents of harmonist/integration-prompt.md
# 4. Follow the AI's walkthrough — it will ask about your
#    project's domain and roles, then wire everything up.
# 5. Start a NEW chat when integration is done.
```

That's it. The AI reads `harmonist/agents/index.json`, picks the
right specialists for your stack, writes a domain-specific
`AGENTS.md` (from `AGENTS.template.md`), bootstraps `.cursor/memory/`,
installs the enforcement hooks, and records the integration state in
`.cursor/pack-version.json`.

### Option 2 — Integrate via CLI (no Cursor needed)

```bash
cd your-project/
git clone https://github.com/GammaLabTechnologies/harmonist.git
python3 harmonist/agents/scripts/integrate.py --pack harmonist --project .
```

(If your pack folder has a different name, pass it via `--pack <PACK_DIR>`.)

On **native Windows** (PowerShell / cmd, no WSL or Git Bash), use the
Python launcher — every script is pure stdlib and cross-platform:

```powershell
cd your-project\
git clone https://github.com/GammaLabTechnologies/harmonist.git
py -3 harmonist\agents\scripts\integrate.py --pack harmonist --project .
```

### Option 3 — Manual integration

See [`GUIDE_EN.md`](GUIDE_EN.md). Note: hand-copying files produces an
unenforced setup (no hooks, rules, or `.gitignore` hardening), so the
guide routes the "manual" path through `integrate.py` +
`verify_integration.py` and lists what they automate.

---

## Architecture

```
                     ┌────────────────────────────┐
                     │        AGENTS.md           │
                     │      Orchestrator          │
                     │                            │
                     │  Protocol · Hook Phases    │
                     │  Invariants · Memory       │
                     └─────────────┬──────────────┘
                                   │  reads
                                   ▼
                     ┌────────────────────────────┐
                     │   agents/index.json        │   ← generated
                     │                            │
                     │   by category · by tag     │   ← routing table
                     │   193 entries              │
                     └─────────────┬──────────────┘
                                   │  routes to
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
     ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
     │ orchestration│      │   review     │      │   persona    │
     │ (scout,      │      │ (strict,     │      │ (engineering,│
     │  repo-map)   │      │  readonly)   │      │  design, …)  │
     └──────────────┘      └──────────────┘      └──────────────┘
                                   │                      │
            ┌──────────────────────┴──────────────────────┐
            ▼                                             ▼
     ┌──────────────┐                             ┌──────────────┐
     │ Review gates │                             │   .cursor/   │
     │              │                             │   memory/    │
     │ qa · sec     │                             │              │
     │ sre · perf   │                             │ session      │
     │ regression   │                             │ decisions    │
     └──────────────┘                             │ patterns     │
                                                  └──────────────┘
```

### How it works

1. **Single unified pool.** Every agent lives at
   `agents/<category>/<slug>.md` with the same [Schema v2](agents/SCHEMA.md)
   frontmatter. One schema, one catalogue.
2. **Data-driven routing.** The orchestrator never hard-codes slugs.
   It extracts task tags ("payments", "react", "solidity"),
   intersects with `agents/index.json`, filters by the project's
   declared `domains` and `roles`, and picks the right specialist.
3. **Protocol tiers by metadata.** `protocol: strict` agents
   (orchestration + review) are mandatory gates that run on triggers.
   `protocol: persona` agents are free-form specialists with domain
   depth.
4. **Hook-observed execution.** `sessionStart`, `afterFileEdit`,
   `subagentStart`, `subagentStop`, `beforeShellExecution`, and `stop`
   hooks track the full lifecycle. The `stop` hook is the gate; the
   `beforeShellExecution` hook is a human-in-the-loop gate on destructive
   commands.
5. **Persistent memory.** Between sessions, state / decisions /
   patterns live under `.cursor/memory/`, linked by correlation IDs.
   The next session reads the last three state snapshots and three
   decisions before planning.

---

## The 193-agent catalogue

Every count below is mirrored from `agents/index.json` and verified by
`check_pack_health.py` — the table and the index cannot drift.

| Category            | Count | Protocol | Focus                                               |
|---------------------|-------|----------|-----------------------------------------------------|
| `orchestration`     |   2   | strict   | Scout before implementation, route to right agent   |
| `review`            |   6   | strict   | Readonly reviewers — security, quality, QA, SRE, regression, a11y |
| `engineering`       |  46   | persona  | Backend, frontend, DevOps, data, AI, embedded, Solidity, LLM eval |
| `design`            |   8   | persona  | UI/UX, brand, accessibility, visual storytelling    |
| `testing`           |   8   | persona  | QA, performance, API testing, evidence collection   |
| `product`           |   5   | persona  | Product management, sprints, feedback, trends       |
| `project-management`|   7   | persona  | Planning, studio production, coordination           |
| `marketing`         |  30   | persona  | Growth, SEO, content, social, Douyin/WeChat/Xiaohongshu |
| `paid-media`        |   7   | persona  | PPC, tracking, campaign audits                      |
| `sales`             |   8   | persona  | Outbound, deals, discovery, proposals               |
| `finance`           |   6   | persona  | FPA, bookkeeping, tax, investments                  |
| `support`           |   5   | persona  | Customer support, compliance, analytics             |
| `academic`          |   5   | persona  | Research, psychology, history, anthropology         |
| `game-development`  |  20   | persona  | Unity, Unreal, Godot, Roblox, Blender               |
| `spatial-computing` |   6   | persona  | visionOS, WebXR, Metal, XR interaction              |
| `specialized`       |  24   | persona  | Blockchain audit, MCP builder, Salesforce, ZK, authorized security testing, privacy engineering, niche |

Each agent carries structured frontmatter: `description`, `tags`,
`domains`, `distinguishes_from` (near-peers), `disambiguation`
(one-line "when to pick this over X"), `version`, and `updated_at`.
The orchestrator reads all of this for tie-breaking when multiple
candidates match a task's tags.

---

## Mechanical enforcement

The enforcement layer is what separates Harmonist from a "nice prompt
pack". It lives in `hooks/` and gets installed into `.cursor/hooks/`
at integration time.

### Six hook phases

| Phase              | What happens                                                                |
|--------------------|-----------------------------------------------------------------------------|
| `sessionStart`     | Bootstrap correlation_id, inject last 3 state / decision memory entries, warn about prior incidents |
| `afterFileEdit`    | Record every write to session state for the stop gate                       |
| `subagentStart`    | Parse `AGENT: <slug>` marker, credit the reviewer, enforce `readonly` capability scoping, and cap concurrent subagents |
| `subagentStop`     | Record verdict, update telemetry                                            |
| `beforeShellExecution` | **HITL gate.** Match the command against the dangerous-command patterns; `ask` for human confirmation (or `deny`) before a destructive command runs |
| `stop`             | **The gate.** Verify reviewers ran, memory updated, protocol satisfied. Return `followup_message` if not. |

### What the stop gate actually checks

If the session touched any file outside ignored patterns:

1. At least one `category: review` agent was invoked via Task.
2. Specifically `qa-verifier` was invoked.
3. `.cursor/memory/session-handoff.md` was updated during the session.

If any check fails, the hook returns `followup_message` telling the AI
exactly what's missing. `loop_limit: 3` caps retries. On exhaustion,
the incident is persisted to `.cursor/hooks/.state/incidents.json`
and surfaced in the next session as an unmissable banner.

### PROTOCOL-SKIP escape hatch

For genuinely trivial turns (typo fix in a comment, markdown
rewording), the AI can emit `PROTOCOL-SKIP: <reason>` to bypass
the gate. The hook logs it. If the skip rate crosses a threshold
(default `> 25%` of all completions, min 5 skips), the next session
starts with a warning about abuse. Easy to opt out of cleanly;
hard to abuse quietly.

---

## Structured validated memory

Memory is a contract, not free-form markdown. Every entry is a YAML
block delimited by `<!-- memory-entry:start -->` /
`<!-- memory-entry:end -->` with required fields: `id`,
`correlation_id`, `at`, `kind`, `status`, `author`, `summary`.

Three files with explicit roles:

| File                    | `kind`     | Purpose                              |
|-------------------------|------------|--------------------------------------|
| `session-handoff.md`    | `state`    | Project state snapshot. Latest = authoritative. |
| `decisions.md`          | `decision` | Append-only architectural decisions. |
| `patterns.md`           | `pattern`  | Lessons learned — what worked, what didn't. |

### CLI as the only write path

```bash
python3 .cursor/memory/memory.py append \
  --file session-handoff --kind state --status done \
  --summary "Integrated Stripe webhook handler" \
  --tags payments,backend \
  --body-file /tmp/handoff-body.md
```

The CLI:

- Generates `id` and `at` deterministically.
- Reads the active `correlation_id` from the hooks (not from the LLM).
- Validates the entry against `memory/SCHEMA.md` before writing.
- Scans the body for ~30 secret patterns and rejects leaks.
- Refuses duplicates unless `--allow-duplicate`.

### Search, rotate, dedupe

```bash
python3 .cursor/memory/memory.py search --tag payments
python3 .cursor/memory/memory.py latest --file session-handoff --n 5
python3 .cursor/memory/memory.py rotate --keep-last 50
```

The `rotate` verb archives older entries to a sibling `.archive.md`
file while keeping the last N live. Both archive and live pass the
validator.

---

## Local repo map (code intelligence)

Most of an agent's budget on an unfamiliar codebase is spent *re-discovering*
it — grep, glob, Read, repeat. `repomap.py` builds a **local, zero-dependency
code map** (pure Python stdlib: `ast` + regex + `sqlite3` — no tree-sitter, no
Node, no native build) so `repo-scout` and the orchestrator **query structure
instead of scanning files**:

```bash
python3 .cursor/repomap/repomap.py build               # index symbols + import graph
python3 .cursor/repomap/repomap.py explore "<question / symbol names>"  # relevant symbols, grouped by file
python3 .cursor/repomap/repomap.py search <SymbolName>  # exact location + signature
python3 .cursor/repomap/repomap.py dependents <file>    # upstream — who imports this
python3 .cursor/repomap/repomap.py impact   <files...>  # transitive blast radius of a change
python3 .cursor/repomap/repomap.py affected <files...>  # which test files a change can break
python3 .cursor/repomap/repomap.py refresh              # incremental (only changed files)
```

Python files are parsed precisely via the standard `ast`; other languages use
lightweight name-based extraction. The index lives at `.cursor/repomap/` (built
during integration, gitignored, refreshed incrementally by file hash).

**Where it pays off:**

- **Cheaper scouting.** `repo-scout` answers `bounded_context`,
  `integration_points`, and `key_tests` from the map in a couple of calls
  instead of a grep/Read discovery loop.
- **Impact-aware enforcement.** The `stop` hook can require that the tests a
  change actually affects were run — set `require_affected_tests: true` in
  `.cursor/hooks/config.json`. `bg-regression-runner` uses the same
  `affected` query to run only the relevant tests, and `sessionStart` warns
  when the map is stale.

This is Harmonist's own clean-room take on the "query a code graph, don't
grep" idea — built to its zero-dependency, drop-in-a-box constraints, and
wired into the enforcement gate rather than bolted on as a separate runtime.

---

## Supply-chain integrity

All runtime-shipped content has a sha256 entry in `MANIFEST.sha256`:
`agents/`, `hooks/`, `memory/`, `playbooks/`, and the root docs. CI
configs (`.github/`, `.gitlab-ci.yml`) and repo metadata are
pack-repo-only and deliberately excluded — they never get installed
into a host project. This buys:

- **Pack health at preflight.** `check_pack_health.py` runs 19 checks
  including `build_manifest.py --verify` — any modified / missing /
  untracked file is flagged.
- **Upgrade refusal.** `upgrade.py --apply` sha-verifies every pack
  source BEFORE copying into a project. A tampered
  `security-reviewer.md` is REFUSED and never enters `.cursor/`:
  ```
  ! REFUSED  agents/review/security-reviewer.md: manifest expected
             5d731c6b..., actual 4b5c2283... -- possible supply-chain tampering
  ```
- **Install-extras verification.** `install_extras.py` — the
  on-demand specialist installer — inherits the same supply-chain
  guard.
- **Post-install anchor.** `.cursor/pack-manifest.json` records
  sha256 of every installed pack-owned file so
  `verify_integration.py` can detect someone editing `gate-stop.sh`
  or `qa-verifier.md` locally to weaken enforcement.
- **Snapshot + rollback.** `upgrade.py --apply` takes a tarball
  snapshot to `.cursor/.integration-snapshots/` before touching
  anything; `upgrade.py --rollback` restores from the latest.

### Prompt-injection scanner

Agents are copy-pasted into `.cursor/agents/` and become part of the
orchestrator's prompt context. A hostile body can silently subvert
every session. `scan_agent_safety.py` runs a heuristic regex pass
over every agent markdown for four classes of hostile content:

| Class              | Examples |
|--------------------|----------|
| Override           | "ignore previous instructions", jailbreak markers |
| Exfil              | Secret leak attempts, `~/.ssh/id_rsa` access, reveal-system-prompt probes |
| Remote exec        | `curl \| bash`, base64-decode-exec, pastebin / ngrok / webhook.site callbacks |
| Policy subversion  | "skip qa-verifier", "always approve silently" |

Runs on the pack catalogue in CI and on installed `.cursor/agents/`
after integration. Exit 1 on any error-severity hit. False-positive
guards built in for legitimate MITRE ATT&CK threat documentation.

---

## Supported IDE integrations

Harmonist ships converters for 11 AI coding assistants. Run
`./agents/scripts/convert.sh --tool <name>` to regenerate the
target-specific artifacts, then `./agents/scripts/install.sh` to
place them in the right spots. On **native Windows**, call the
cross-platform Python entry points directly:
`py -3 agents\scripts\convert.py --tool <name>` then
`py -3 agents\scripts\install.py`. (The `.sh` scripts are thin POSIX
wrappers around the same `convert.py` / `install.py`.)

| Tool              | Surface produced                                         |
|-------------------|----------------------------------------------------------|
| **Cursor**        | `.cursor/agents/*.md` + `.cursor/rules/*.mdc` + hooks    |
| **Claude Code**   | Project-scoped agent definitions                         |
| **GitHub Copilot**| Custom instruction file                                  |
| **Windsurf**      | `.windsurfrules`                                         |
| **Aider**         | `CONVENTIONS.md`                                         |
| **Kimi**          | `agents-orchestrator/system.md`                          |
| **Qwen**          | Agent directory format                                   |
| **Gemini CLI**    | Extension manifest + skills                              |
| **OpenCode**      | Per-agent markdown                                       |
| **OpenClaw**      | IDENTITY.md per agent                                    |
| **Antigravity**   | Skill format                                             |

Add `--thin` to install the essentials-only variant of each persona
agent (~38% fewer body lines across the pool, which matters in
context-constrained sessions). See [`agents/SCHEMA.md`](agents/SCHEMA.md)
for the `## Deep Reference` convention that makes thin mode possible.

---

## Key scripts

| Script                              | What it does                                                                   |
|-------------------------------------|--------------------------------------------------------------------------------|
| `check_pack_health.py`              | 19 preflight checks (version, manifest, lint, migrator idempotency, etc.)     |
| `lint_agents.py`                    | Validate every agent against Schema v2 — 0 errors required                    |
| `build_index.py`                    | Regenerate `agents/index.json` (routing table)                                |
| `build_manifest.py`                 | Regenerate `MANIFEST.sha256` (supply-chain anchor)                            |
| `integrate.py`                      | Full integration into a target project                                        |
| `upgrade.py`                        | Roll an integrated project forward to a newer pack version, with snapshots   |
| `install_extras.py`                 | Add specialists to `.cursor/agents/` by slug, role bundle, or tag — sha-verified |
| `verify_integration.py`             | Objective post-integration audit — what's missing, what's customised         |
| `onboard.py`                        | Guided tour of an already-integrated project for a teammate joining it       |
| `scan_agent_safety.py`              | Prompt-injection / exfil scanner for catalogue + installed agents            |
| `scan_memory_leaks.py`              | Audit git history for accidentally-committed memory files                    |
| `scan_rules_conflicts.py`           | Detect phantom slugs, duplicate-purpose rules, protocol contradictions       |
| `insert_deep_ref_marker.py`         | Add `## Deep Reference` cut point to long persona agents                     |
| `extract_essentials.py`             | Produce the thin variant of a persona agent                                  |
| `report_usage.py`                   | Render local agent-usage telemetry; recommend dead-balance removal           |
| `repomap.py`                        | Zero-dep local code map — symbols + import graph; `explore` / `search` / `impact` / `affected` so the scout queries instead of grepping |

Full script index: [`agents/scripts/`](agents/scripts/).

---

## Documentation

| File | Purpose |
|------|---------|
| [`AGENTS.template.md`](AGENTS.template.md) | Orchestrator TEMPLATE — protocol, hook phases, memory, resilience. Becomes the project-specific `AGENTS.md` (paths substituted, domain customized) in every integrated project. Not an active rule inside the pack folder. |
| [`GUIDE_EN.md`](GUIDE_EN.md) | Condensed walkthrough for first-time users. |
| [`integration-prompt.md`](integration-prompt.md) | The one-shot prompt to paste into Cursor Agent mode for fully automated integration. |
| [`agents/SCHEMA.md`](agents/SCHEMA.md) | Frontmatter contract (Schema v2) every agent file must satisfy. |
| [`agents/STYLE.md`](agents/STYLE.md) | How agent bodies should read — canonical shapes, anti-patterns, retrofit checklist. |
| [`agents/TAGS.md`](agents/TAGS.md) | Curated tag vocabulary (253 tags organized by layer). |
| [`memory/SCHEMA.md`](memory/SCHEMA.md) | Memory entry schema (v1), correlation-ID format, validation rules. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute, PR checklist, release process. |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting policy, scope, response timelines. |
| [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) | Community standards. |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history in Keep-a-Changelog format. |
| [`playbooks/`](playbooks/) | Optional NEXUS 7-phase lifecycle with phase-specific runbooks for startups, enterprises, incident response, and marketing campaigns. |

---

## Testing

The enforcement layer, memory CLI, and every script ship with
exhaustive test coverage. `550+ test assertions` across hooks, memory,
and shell-based integration suites — all green in CI on every push.

| Suite                              | Assertions | Covers                                              |
|------------------------------------|-----------:|-----------------------------------------------------|
| Hook tests                         |         54 | All 6 phases (incl. the `beforeShellExecution` HITL gate), state locking, capability scoping, loop-limit / fail-closed, incident surfacing, concurrency cap, cross-platform runner parity |
| Memory tests                       |         33 | Append, validate, search, rotate, dedupe, migrations, secret-pattern scanner (incl. first-match + extra-field bypass) |
| Integration + upgrade + install_extras  |        90+ | End-to-end integration, snapshots, rollback, on-demand specialist install, post-install drift detection |
| Supply-chain integrity             |         23 | MANIFEST verification, tampered-source refusal, pack-manifest tracking |
| Enforcement extras                 |         40+ | telemetry-webhook (scheme allowlist + retry), repo-map code index, git pre-commit guard, hardening-checklist validator |
| 18 other script suites             |        300+| lint, extract, freshness, rules-conflicts, memory-privacy, telemetry, project-context, regression detection, py-guard sync, smoke test, onboarding, etc. |

Run the full regression locally:

```bash
python3 agents/scripts/check_pack_health.py       # 19 preflight checks
bash hooks/tests/run-hook-tests.sh                # 54 scenarios
bash memory/tests/run-memory-tests.sh             # 33 scenarios
for t in agents/scripts/test_*.sh; do bash "$t"; done  # all script suites
```

---

## FAQ

### Is this another LangChain / AutoGen / CrewAI alternative?

No. Those are **frameworks you build an agentic application on top of**
— they provide abstractions for chaining LLM calls, tool use, and
orchestration logic.

Harmonist is **a pack you drop into an existing project** to make your
AI assistant follow a protocol. It doesn't replace your app's runtime;
it installs next to your code and intercepts how the coding assistant
(Cursor, Claude Code, Copilot, etc.) interacts with your project.

The two can coexist. Harmonist governs the *coding workflow*; a
LangChain / AutoGen / CrewAI app is something the workflow might
*produce*.

### Why mechanical enforcement instead of prompt guidance?

Because prompt guidance is advisory and AI models treat it that way.
"Always run QA before marking done" in a prompt is aspirational. A
stop hook that refuses to let the turn finish until QA ran is a
contract. We chose contracts.

### Does it work without Cursor?

Yes. Cursor is the primary integration because of its subagent and
hooks support, but the pack ships adapters for 10 other tools (see
[Supported IDE integrations](#supported-ide-integrations)). The
enforcement layer is strongest with Cursor; in other tools it falls
back to convention-plus-validation.

### Can I customize the protocol?

Yes, and you should. The project-level `AGENTS.md` is the canonical
source for your domain's invariants, stack, modules, and resilience
policies. The orchestrator gives *it* precedence over any persona
agent body. Persona agents adapt to your rules; your rules don't
adapt to personas.

Pack-owned sections of `AGENTS.md` are delimited by
`<!-- pack-owned -->` markers and upgrade-replaced by `upgrade.py`.
Everything outside those markers is yours — untouched by upgrades.

### Is telemetry enabled by default? What does it collect?

Local telemetry is enabled by default. It records per-agent
invocation counts, session counts, and gate-allow / PROTOCOL-SKIP
counters to `.cursor/telemetry/agent-usage.json`. **Nothing is
uploaded anywhere — it's a local file, gitignored, and readable in
plain JSON.**

Disable completely by setting `telemetry_enabled: false` in
`.cursor/hooks/config.json`.

### Why 193 agents? Isn't that too many?

Most projects activate 10–20 specialists for their actual working
roles. The other ~170 are available but invisible — filtered out by
the `domains × roles × tags` intersection during routing. A TON
blockchain project never sees WeChat or Xiaohongshu marketing
agents. A Web SaaS never sees Solidity or ZK specialists.

The size of the catalogue is an **asset**, not a cost — it means when
your project grows into a new role (adds marketing, adds support,
pivots to a new platform), the specialists are already curated and
vetted. Install them on demand with:
```bash
python3 agents/scripts/install_extras.py --role marketing
```

### How do I add my own agent?

See [`agents/STYLE.md`](agents/STYLE.md) for the canonical persona
template and [`agents/SCHEMA.md`](agents/SCHEMA.md) for the
frontmatter contract. Create the file under the right category
directory, run `python3 agents/scripts/lint_agents.py` to validate,
regenerate the index (`build_index.py`) and manifest
(`build_manifest.py`), and commit.

### What's the license and who owns the code?

MIT — [see LICENSE](LICENSE). Copyright © 2026 GammaLab.
Free to use, modify, fork, and ship commercial products built on
top of.

### I found a security issue. Where do I report it?

Open a **private security advisory** via the repository's Security
tab — see [`SECURITY.md`](SECURITY.md). Do not open a public issue
for security-sensitive reports.

---

## Contributing

Harmonist welcomes contributions that raise the bar:

- **New agents** for domains not yet covered.
- **Tighter enforcement** in the hooks.
- **New integrations** for AI assistants we don't support yet.
- **Documentation** that helps someone go from "I just heard of this"
  to "I integrated it into a real project" faster.

Before opening a PR, read [`CONTRIBUTING.md`](CONTRIBUTING.md). For
non-trivial work, **open an issue first** to align on approach. Drive-
by reformatting will be closed; content-driven PRs will be reviewed
quickly.

---

## Security

- Private vulnerability reports: see [`SECURITY.md`](SECURITY.md).
- Supply-chain audit: run `python3 agents/scripts/check_pack_health.py`
  after every `git pull`.
- Community standards: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

---

## License

Harmonist is released under the [MIT License](LICENSE) —
**Copyright © 2026 GammaLab**. Free for commercial use, modification,
and distribution.

---

## About GammaLab

[**GammaLab**](https://gammalab.ae) is a technology company working on
robotics, artificial intelligence, and machine cognition. Harmonist
is our first open-source release — a tool we built to enforce
engineering discipline in AI-assisted development, and one we use
daily across our own internal projects.

If you ship production code with AI assistance and you care about
making that code safe, auditable, and aligned with your project's
actual rules — Harmonist is for you.

- **Website:** [gammalab.ae](https://gammalab.ae)
- **GitHub:** [@GammaLabTechnologies](https://github.com/GammaLabTechnologies)
- **Contact:** Open an issue for public discussion; use the
  repository's Security tab for private reports.

---

<div align="center">

**If Harmonist helps you ship safer AI-assisted code,
star the repository — it's the cheapest signal you can send
the maintainers that this work matters.** ⭐

Made with engineering discipline by [GammaLab](https://gammalab.ae).

</div>
