# Session Handoff

Rolling snapshot of project state. Every task appends a new `<!-- memory-entry -->`
block at the end. The most recent block is authoritative.

Read the `latest` block at the start of every session. Never edit past
blocks — write a new one with updated state.

See `SCHEMA.md` for the entry contract. Use `memory.py append` to add new
entries; direct hand-edits must still pass `memory.py validate`.

<!-- memory-entry:start -->
---
schema_version: 1
id: 0-0-state
correlation_id: 0-0
at: 1970-01-01T00:00:00Z
kind: state
status: done
author: human
summary: Template bootstrap. Replace this entry with your first real snapshot.
tags: [template]
---

## Services / Current State
- [list running services, hostnames, deploy targets, DB state]

## Tech Stack
- [language, framework, ORM, DB, cache]

## Recent Changes
- [description of recent changes]

## Open Issues / Tech Debt
- [known problems, unfinished work]

## Deploy Protocol
- [step-by-step deploy procedure, or "TBD"]

<!-- memory-entry:end -->
