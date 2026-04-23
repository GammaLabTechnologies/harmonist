# Contributing to Harmonist

Thanks for taking the time to contribute. Harmonist is a portable AI
agent orchestration pack — changes here affect every project that
integrates it, so the bar for merging is intentionally strict.

This file is the **short contract**. Technical depth (agent schema,
style, protocol) lives in:

- [`agents/SCHEMA.md`](agents/SCHEMA.md) — frontmatter contract every
  agent must satisfy.
- [`agents/STYLE.md`](agents/STYLE.md) — how the body of an agent
  should read (required sections, anti-patterns, retrofit checklist).
- [`README.md`](README.md) — architecture overview and scripts reference.

## Before you start

1. Read [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). By participating,
   you agree to it.
2. For **non-trivial work** (new agent, new script, behaviour change,
   hook change), **open an issue first** describing the plan. This saves
   everyone time — we can flag concerns before you write code.
3. Drive-by cosmetic edits (emoji removal, rewording 179 personas at
   once) will be closed without review. Do content-driven edits only.

## How to contribute

### Add or update an agent

1. Pick the right `agents/<category>/` directory. Categories are fixed
   (see `agents/SCHEMA.md#category-enum`); propose a new one only with a
   strong case.
2. Start from [`agents/STYLE.md`](agents/STYLE.md)'s lightweight template.
   Use plain ASCII headings, no personality-theatre sections.
3. Frontmatter must satisfy Schema v2. Run the linter:
   ```
   python3 agents/scripts/lint_agents.py
   ```
4. Add a `## Deep Reference` marker if the body exceeds ~80 non-blank
   lines (the `--thin` converter depends on it). Run:
   ```
   python3 agents/scripts/insert_deep_ref_marker.py agents/<cat>/<slug>.md
   ```
5. Regenerate the routing index and the manifest:
   ```
   python3 agents/scripts/build_index.py
   python3 agents/scripts/build_manifest.py
   ```

### Change scripts / hooks / enforcement

1. Every script change must pass:
   ```
   python3 agents/scripts/check_pack_health.py        # 18 checks
   bash hooks/tests/run-hook-tests.sh                 # 30 scenarios
   bash memory/tests/run-memory-tests.sh              # 29 scenarios
   ```
2. Any new behaviour needs a matching test case in the relevant
   `test_*.sh` under `agents/scripts/` or `hooks/tests/`.
3. Python scripts include a 3.9+ version guard. Never add third-party
   dependencies — stdlib only.

### Fix bugs

1. Reproduce first. A bug report without reproduction steps will be
   closed asking for one.
2. Add a failing test before the fix, so the regression stays closed.
3. For security-relevant bugs, follow [`SECURITY.md`](SECURITY.md) and
   open a **private security advisory** instead of a public issue.

## Pull request checklist

- [ ] Linter clean: `python3 agents/scripts/lint_agents.py` — 0 errors.
- [ ] Pack healthy: `python3 agents/scripts/check_pack_health.py` — 18/18.
- [ ] Relevant test suites pass (see the list under "Change scripts").
- [ ] Index and manifest regenerated and committed.
- [ ] If a user-facing behaviour changed: `CHANGELOG.md` updated.
- [ ] If schema changed: version bumped + migrator updated.
- [ ] PR description explains **why**, not just **what**.

## What the maintainers will reject

- PRs that disable, bypass, or weaken the enforcement layer
  (`qa-verifier`, hooks, memory validator) without an explicit review
  discussion first.
- Edits to shipped `agents/review/*.md` or `agents/orchestration/*.md`
  that change their strict behaviour, unless scoped to the exact issue
  being fixed.
- Committed build output (converted agent files, generated docs) —
  these are produced by `convert.sh` locally and are gitignored.
- Bulk reformatting of the persona catalogue in a single PR — each
  category gets its own PR with the before/after lint output.
- Adding third-party Python dependencies. The pack is stdlib-only by
  design.

## Release process (maintainers only)

1. `python3 agents/scripts/check_pack_health.py` — must be 18/18.
2. Full regression: `bash` every `agents/scripts/test_*.sh`,
   `hooks/tests/run-hook-tests.sh`, `memory/tests/run-memory-tests.sh`.
3. Update `CHANGELOG.md`: move `[Unreleased]` entries under a new
   version heading with today's date (ISO).
4. Bump `VERSION` (SemVer). Breaking schema changes require a major
   bump and a migrator registered in `migrate_schema.py` or
   `memory/migrations.py`.
5. Regenerate `MANIFEST.sha256` and commit.
6. Tag the release as `vX.Y.Z` and push.

## Questions

Open an issue on the repository. For private matters, use a security
advisory (see `SECURITY.md`).
