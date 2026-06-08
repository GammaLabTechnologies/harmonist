# Changelog

All notable changes to Harmonist land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Integrated projects record the pack version they adopted in
`.cursor/pack-version.json`. Use `agents/scripts/upgrade.py` to roll a
project forward; see the "Documentation" table in `README.md` for
what the upgrade tool is allowed to touch.

---

## [Unreleased]

### Reliability & security hardening (full-audit pass)

A codebase-wide audit (hooks runtime, install/upgrade lifecycle, memory, repo
map, scanners, and the telemetry webhook) found and fixed several real defects.
The headline theme: the enforcement gate now **fails closed** and can no longer
be silently bypassed.

#### Security

- **Telemetry webhook no longer enables SSRF / local-file disclosure.**
  `telemetry_webhook.py` now refuses any non-`http(s)` destination (a
  config/env-supplied `file://…` URL previously made `urllib` read a local
  file) and does **not** follow redirects (a 30x `Location:` could pivot to
  `file://` or an internal host).
- **Webhook now retries transient failures** (network errors, HTTP 429, 5xx)
  with exponential backoff + jitter (`--attempts`, default 3) — matching the
  documented resilience policy instead of giving up after one POST.
- **Secret scanner no longer stops at the first match per pattern.** A
  placeholder example (e.g. `<AKIA…>`) earlier in an entry used to disable the
  whole pattern, letting a real same-type secret through; it now scans every
  match. Added the canonical `AWS_SECRET_ACCESS_KEY=…` `.env` form, and now
  scans **all** free-text fields (`--scope` / `--author-detail` / `--links` /
  `--tags`), not just summary/body.
- **Supply-chain drift detection is now actually implemented.**
  `verify_integration.py` reads `.cursor/pack-manifest.json` and re-hashes every
  pack-owned file, so post-install tampering of `gate-stop.sh`,
  `hook_runner.py`, or `security-reviewer.md` is detected instead of passing
  verification clean.
- **HITL dangerous-command patterns hardened** to catch `rm -rf /usr`, long
  options (`--recursive --force`, `--no-preserve-root`), and reordered
  `dd of=/dev/…`; and the gate now also exists on the **POSIX hook path**
  (`gate-shell.sh`), which previously had no dangerous-command protection.

#### Fixed

- **`stop` gate fails CLOSED.** Every hook phase is wrapped so an internal
  error or a hung/slow memory validator (now `timeout`-bounded) emits a
  conservative followup instead of nothing — Cursor reads "no output" as "no
  enforcement", which let a crash silently end the turn.
- **`session.json` is now locked** (cross-platform advisory lock) around the
  full read-modify-write. Concurrent `afterFileEdit` / `subagentStart` hooks
  could previously lose updates — dropping a recorded write (gate fails open)
  or letting two launches both clear the concurrency cap.
- **`afterFileEdit` records the session-handoff write on the Python path.**
  A relative `.cursor/memory/session-handoff.md` path was matched by
  `skip_path_patterns` *before* the memory bookkeeping, so the handoff
  requirement could never be satisfied and every code task looped to
  exhaustion. Memory paths are now checked first (matching the `.sh` path).
- **`PROTOCOL-SKIP` detection is scoped to the agent's final message.** It was
  matched anywhere in the serialized hook input, including the seed/followup
  template text echoed back in context — a fail-open.
- **`run_regression.py`**: a partial run (e.g. `--steps lint`) no longer flips
  `last_regression_ok` true while the project's test step was skipped; and a
  timed-out step now kills its **whole process tree** (orphans previously
  survived a `shell=True` timeout).
- **Repo map** no longer leaves stale/missing cross-file edges after an
  incremental refresh when files are added/removed; the affected-tests gate
  path-normalization (`_relativize` / `repomap._norm`) no longer corrupts
  `.github/…` / `.eslintrc` paths via `str.lstrip("./")`.
- **`trivial_path_patterns`**: `docs/` / `documentation/` are trivial only for
  documentation/asset content — code under those directories (e.g.
  `docs/conf.py`, `src/docs/handler.py`) is reviewed again.
- **Scanners** (`scan_rules_conflicts.py`, `scan_agent_freshness.py`) parse
  frontmatter on Windows (CRLF) checkouts instead of silently skipping files.
- **`integrate.py`** reads the verifier's `errors`/`warnings` summary keys
  (were singular, always reported 0).

#### Changed

- Python↔POSIX hook parity unified: prompt field-probing for the `AGENT:`
  marker, `subagentStop` selection (LIFO) + state fields, and the readonly
  capability lookup now searches nested agent category folders.
- All hook state reads/writes pin `encoding="utf-8"` (Windows non-ASCII safety).
- `verify_integration.py` now expects all **6** hook events (incl.
  `beforeShellExecution`); docs updated from "five hooks" to six.

---

Cross-platform install path. Harmonist now integrates on **native Windows**
(no WSL / Git Bash required), alongside macOS and Linux, using only the
Python stdlib.

### Added

- **`agents/scripts/convert.py` and `agents/scripts/install.py`** — pure-Python,
  cross-platform implementations of the converters and the multi-tool
  installer. `convert.sh` / `install.sh` are now thin POSIX wrappers that
  delegate to them, so existing shell invocations and CI keep working while
  Windows users run `python agents\scripts\convert.py` / `install.py` directly.
- **OS-aware hook interpreter.** `upgrade.py` renders the project's
  `.cursor/hooks.json` with a Python launcher that actually exists on the host
  (`py -3` / `python` on Windows, `python3` on POSIX) instead of a hard-coded
  `python3` that is frequently absent on native Windows.
- **`.gitattributes`** pinning `eol=lf` for every text file, so a Windows
  checkout cannot rewrite line endings and break `MANIFEST.sha256`
  verification or the shell hooks.
- **`agents/scripts/test_cross_platform.py`** — a stdlib-only regression
  (convert, install, hooks render, and a real `upgrade --apply` + smoke test)
  that runs identically on every OS.
- **Native-Windows CI job** plus a GitLab `cross-platform` job exercising the
  Python entry points and the end-to-end smoke on `windows-latest`.

### Added

- **Git pre-commit enforcement guard** (`hooks/scripts/git-pre-commit.sh` +
  idempotent `install-git-hooks.sh`). Closes the gap the IDE `stop` gate can't
  see: a `git commit` from the terminal. The guard refuses a commit that stages
  review-worthy code (not skipped / not trivial — same definition as the stop
  gate) while the current task has unreviewed writes (required reviewer not yet
  seen) or an unresolved protocol-exhausted incident. Fail-open on missing state
  (fresh clone / CI), backs up an existing pre-commit, never touches git config,
  `git commit --no-verify` bypass. Installed by `upgrade.py`; activate with
  `bash .cursor/hooks/scripts/install-git-hooks.sh`. Covered by `test_git_hooks.sh`.
- **General skills** for `playbooks/skills/`: `write-a-skill` (meta — how to
  author skills, making the library self-extending), `to-prd` (idea → PRD), and
  `brainstorming` (diverge/converge with trade-offs).
- **`privacy-engineer` specialist agent** (catalogue 192 → 193). Privacy-by-design
  / data-protection engineering — data mapping, minimization, consent, retention
  & deletion, GDPR/CCPA-aligned PII handling, telemetry hygiene — filling a real
  catalogue gap (offensive security existed; privacy engineering did not). Adds a
  `data-protection` tag.
- **Structured security & privacy hardening checklist** (`playbooks/checklists/`):
  a clean-room, schema-validated JSON checklist (8 sections, priority-tagged
  `essential`/`recommended`/`advanced`) covering appsec, secrets, injection, data
  protection/privacy, supply chain, infra, and observability — plus a pure-stdlib
  `validate.py` (no PyYAML/jsonschema) and a `security-privacy-hardening-checklist`
  skill the security agents apply. Validated in CI.
- **Authorized-security agent crew (catalogue 186 → 192).** Six clean-room
  Schema-v2 personas under `agents/specialized/` for *authorized* security
  work: `security-recon-mapper`, `security-web-app-pentester`,
  `security-vulnerability-triage`, `security-exploit-developer`,
  `security-red-team-operator`, `security-pentest-report-writer`. Each carries
  explicit authorization + scope guardrails (the opposite of "never question
  authorization"). Adds six security tags to the curated vocabulary.
- **Delegation-context gate** (`require_delegation_context`, opt-in). The
  `subagentStart` hook DENIES a `task` whose handoff (prompt minus the
  `AGENT:` marker) is below `min_delegation_chars` — forcing the orchestrator
  to pass real context (target/scope/sub-goal/success) so subagents don't
  guess and redo work. `AGENTS.md` now codifies the handoff package + a
  no-nested-delegation rule.
- **HITL (human-in-the-loop) gate on dangerous shell commands.** A new
  `beforeShellExecution` hook matches commands against a conservative
  `dangerous_command_patterns` list (root/home/wildcard deletes, force-push,
  disk wipes, fork bombs, pipe-to-shell, destructive SQL) and returns `ask`
  (human confirms) or `deny`. On by default with `ask` — routine commands
  (e.g. `rm -rf node_modules`) pass untouched; only catastrophic ones pause.
  Tune via `hitl_enabled` / `dangerous_command_action` / patterns.
- **Skills library — reusable task playbooks (`playbooks/skills/`).** Small
  on-demand recipes (`secure-code-review`, `authorized-web-pentest`,
  `incident-response`, `dependency-vulnerability-remediation`) the orchestrator
  follows for recurring jobs instead of improvising.
- **Local repo map — zero-dependency code intelligence (`agents/scripts/repomap.py`).**
  A pure-stdlib index (`ast` + regex + `sqlite3`; no tree-sitter / Node / native
  build) of the project's symbols and file-level import graph, so `repo-scout`
  and the orchestrator **query structure instead of grepping**. Verbs:
  `build` / `refresh` (incremental by file hash) / `status` / `search` /
  `explore` / `deps` / `dependents` / `impact` (transitive blast radius) /
  `affected` (impacted test files). Built during `integrate.py`, installed to
  `.cursor/repomap/` by `upgrade.py`, gitignored index. `repo-scout` now queries
  it first. Cross-platform, covered by `test_repomap.sh` + the cross-platform
  suite. A clean-room take on "query a code graph, don't grep", built to
  Harmonist's drop-in, zero-dep constraints — and wired into enforcement:
  - **Impact-aware `stop` gate** (`require_affected_tests`, opt-in): refuses to
    finish a code change until the tests its blast radius actually touches have
    been run (reuses `last_regression_ok`).
  - **`bg-regression-runner`** runs only the affected tests (`repomap affected`).
  - **Repo-map staleness banner** at `sessionStart` (`repomap_staleness_warn`).
- **Mechanical concurrent-subagent cap.** The `subagentStart` hook now
  DENIES a subagent launch once `max_concurrent_subagents` (default 3) are
  already running in the current task, returning `{"permission": "deny"}`.
  This turns the previously advisory "Max 3 concurrent" rule into a real
  gate and stops unbounded parallel fan-out from exhausting RAM (each
  subagent now holds a 1M-context model). A `subagent_stale_seconds`
  (default 900) guard prevents a missed `subagentStop` from permanently
  locking out launches; set the cap to `0` to disable. Implemented in both
  `hook_runner.py` and `record-subagent-start.sh` (parity), with telemetry
  (`summaries.subagent_cap_denials`) and hook tests.

### Fixed

- **`hook_runner.py` was never installed.** `upgrade.py` shipped a
  `hooks.json` that invokes `.cursor/hooks/scripts/hook_runner.py`, but the
  file was missing from the install plan, so the active hook path referenced a
  non-existent script. It is now installed on every OS (the smoke test
  previously masked this by exercising the `.sh` scripts via `bash`).
- **`smoke_test.py`** now drives the cross-platform `hook_runner.py` through
  `sys.executable` instead of shelling out to `bash` + a hard-coded `python3`,
  and uses an OS-neutral sentinel path instead of `/tmp/...`.
- **`check_pack_health.py`** and **`verify_integration.py`** no longer assume
  `python3`/`bash` on `PATH` or POSIX executable bits; lint runs the Python
  linter directly and the exec-bit checks are skipped on Windows.

### Changed

- `hooks.windows.json` is now a genuine Windows variant (uses the `py`
  launcher) for the manual-install path, rather than a copy of the POSIX file.
- **Every agent is pinned to the strongest model.** The `model:` frontmatter
  field is now a concrete Cursor model slug (default `claude-opus-4-8`,
  Claude Opus 4.8) instead of the old `fast` / `inherit` / `reasoning` tier
  abstraction, so dispatched subagents run on the best model rather than
  falling back to the host default. `DEFAULT_MODEL` / `CONCRETE_MODELS` /
  `FIXED_MODELS` in `migrate_schema.py` centralise the choice; the legacy tier
  words remain accepted by the linter and are auto-upgraded by the migrator.
  Enable Cursor **Max Mode** for the 1M-token context window (a global toggle,
  not a model slug).

---

## [1.0.0] — 2026-04-23

Initial public release. Harmonist ships 186 curated agents, a
Schema-v2 frontmatter contract, structured validated memory, and a
hook-driven enforcement layer that cannot be skipped by the LLM.

### Catalogue

- **186 agents across 16 categories.** Unified pool under
  `agents/<category>/<slug>.md`, every file conforming to Schema v2.
  Strict agents (`orchestration`, `review`) drive the mandatory
  gates; persona agents are free-form specialists with domain depth.
- **Data-driven routing.** `agents/index.json` is the single routing
  table the orchestrator consults. Adding or renaming an agent
  requires zero edits to `AGENTS.md`.
- **Disambiguation metadata.** Every agent with near-peers carries
  `distinguishes_from` + a one-line `disambiguation` note so the
  orchestrator can tie-break between look-alike specialists.
- **Domain filter + role filter.** The integration prompt asks for
  both the project's `domains` (blockchain / gamedev / china-market /
  healthcare / etc.) and its working `roles` (engineering, design,
  product, marketing, sales, support, finance, testing, academic) to
  select a sensible starter set of specialists, not a
  one-size-fits-all dump.

### Enforcement

- **Cursor hooks drive the stop gate.** The `stop` hook parses
  `AGENT: <slug>` markers from subagent prompts and refuses to allow
  turn completion if `qa-verifier` has not run, if any required
  reviewer is missing, or if `session-handoff.md` was not updated.
  `loop_limit: 3` caps retries; on exhaustion, an incident is recorded
  in `.cursor/hooks/.state/incidents.json` and surfaced in the next
  `sessionStart` as a `PROTOCOL-EXHAUSTED` banner.
- **Cross-platform runner.** `hooks/scripts/hook_runner.py` is a
  pure-Python implementation of every hook phase, so native Windows
  works out of the box; WSL / macOS / Linux can stay on the POSIX
  `.sh` variants via `hooks/hooks.posix.json`.
- **Capability scoping.** Agents marked `readonly: true` are blocked
  from writing files; violations are logged.
- **PROTOCOL-SKIP abuse guard.** The escape hatch for trivial turns
  is tracked in telemetry; excessive use triggers a warning injected
  into the session bootstrap context.

### Memory

- **Schema v1 memory contract.** `.cursor/memory/*.md` entries are
  YAML blocks delimited by `<!-- memory-entry:start -->` /
  `<!-- memory-entry:end -->` with 7 required frontmatter fields.
  `validate.py` enforces every rule and is wired into the `stop` hook.
- **Hook-generated correlation IDs.** `correlation_id` has the form
  `<session_id>-<task_seq>`, generated at session start
  (`<unix-seconds><pid4>` — collision-safe across parallel sessions)
  and advanced by the stop gate. The LLM cannot invent IDs.
- **CLI as the only write path.** `memory.py append` validates before
  writing, scans for ~30 classes of secrets (AWS / GitHub / Stripe /
  GCP / Azure / Slack / Telegram / Discord / SendGrid / DigitalOcean /
  npm / PyPI / DB DSNs / generic high-entropy tokens / vendor-scoped
  UUIDs for Heroku, Postmark, etc.), and rejects placeholder-fenced
  content correctly so `${STRIPE_KEY}` still writes cleanly.
- **Search + rotate + dedupe.** `memory.py search` filters by tag /
  kind / correlation / summary; `rotate` archives older entries;
  dedupe refuses identical-summary appends unless `--allow-duplicate`.

### Supply-chain integrity

- **MANIFEST.sha256 covers every shipped file.**
  `check_pack_health.py` runs `build_manifest.py --verify` on every
  preflight; a tampered source is flagged.
- **Upgrade guard.** `upgrade.py --apply` sha-verifies every pack
  source BEFORE copying into `.cursor/`. A tampered
  `security-reviewer.md` is REFUSED.
- **Install-extras guard.** `install_extras.py` inherits the same
  sha-verification for on-demand specialist installs.
- **Post-install anchor.** `.cursor/pack-manifest.json` records the
  sha of every installed pack-owned file so `verify_integration.py`
  can detect someone weakening enforcement after install.
- **Snapshot + rollback.** `upgrade.py --apply` takes a pre-apply
  tarball snapshot under `.cursor/.integration-snapshots/`;
  `upgrade.py --rollback` restores from the most recent snapshot.

### Prompt-injection scanner

- `scan_agent_safety.py` runs a heuristic regex pass over every agent
  markdown for four classes of hostile content:
  **override** (jailbreak markers, "ignore previous instructions"),
  **exfil** (secret leak attempts, `.env` access),
  **remote exec** (pastebin / ngrok / webhook.site callbacks,
  `curl | bash`, base64-decode-exec),
  **policy subversion** ("skip qa-verifier", "always approve
  silently"). Runs in CI; exit 1 on any error-severity hit.
  False-positive guards for legitimate MITRE ATT&CK threat docs.

### Integrations

- **11 IDE targets** via converters under `agents/integrations/`:
  Cursor, Claude Code, GitHub Copilot, Windsurf, OpenCode, Aider,
  Kimi, Qwen, Gemini CLI, Antigravity, OpenClaw.
- **Thin-variant mode.** `convert.sh --thin` ships the essentials-only
  body of each persona agent (everything up to `## Deep Reference`);
  typical saving ~38% across the pool.

### Style + schema

- **Schema v2** — `agents/SCHEMA.md`. Required frontmatter plus
  optional metadata (`version`, `updated_at`, `deprecated`,
  `distinguishes_from`, `disambiguation`, `domains`, `color`,
  `emoji`, `vibe`). Slug (filename stem) is the identity key used
  for routing; `name` is a human-readable display label.
- **Style guide** — `agents/STYLE.md`. Defines the two canonical
  shapes (strict vs persona), anti-patterns to avoid (personality
  theatre, adjective soup, emoji-prefixed headings, aspirational
  sections, cross-agent hand-off lists), a lightweight persona
  template, and a retrofit checklist.
- **Deep Reference convention.** Every persona agent with ≥ 80
  non-blank body lines carries a `## Deep Reference` marker. Thin
  variants cut at the marker; `insert_deep_ref_marker.py` adds the
  marker to any long persona automatically.

### Tooling

- `check_pack_health.py` — 18 preflight checks (VERSION parses as
  SemVer, every directory + script present + executable, index /
  manifest fresh, lint clean, migrator idempotent, agent catalogue
  free of deprecated tech and prompt-injection patterns,
  README / AGENTS.md category counts match `index.json`).
- `lint_agents.py` — Schema v2 validator for every agent.
- `build_index.py` — deterministic routing-table generation.
- `build_manifest.py` — sha256 covering every shipped file
  (262 tracked files at 1.0.0).
- `integrate.py` / `upgrade.py` / `deintegrate.py` — full integration
  lifecycle.
- `verify_integration.py` — objective post-install audit.
- `install_extras.py` — on-demand specialist installation by slug /
  role / tag with sha-verification and thin-variant support.
- `scan_agent_safety.py`, `scan_agent_freshness.py`,
  `scan_rules_conflicts.py`, `scan_memory_leaks.py` — four
  complementary scanners for catalogue health and integration hygiene.
- `merge_agents_md.py` — replaces `<!-- pack-owned -->` blocks in a
  project's `AGENTS.md` with the newest pack version, preserving
  project-owned prose between marker pairs.
- `insert_deep_ref_marker.py` — automated cut-point insertion for
  long persona agents.
- `detect_regression_commands.py` — infers project test / lint / build
  commands from manifests for `bg-regression-runner`.
- `onboard.py` — walkthrough for teammates joining an
  already-integrated project.
- `refresh_py_guard.py` — keeps the 3.9+ version guard in sync across
  every entry script.

### Testing

- **~430 test assertions** across hook tests (30 scenarios), memory
  tests (29 scenarios), and 20 shell-based suites under
  `agents/scripts/test_*.sh`. All passing on first clean integration.
- **GitHub Actions CI** runs the full regression on every pull request
  and push to `main`.

### Documentation

- Root-level `README.md`, `AGENTS.md`, `GUIDE_EN.md`,
  `integration-prompt.md`, `CHANGELOG.md`, `LICENSE`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- Agent-level `agents/SCHEMA.md`, `agents/STYLE.md`, `agents/TAGS.md`.
- Memory-level `memory/SCHEMA.md`, `memory/README.md`.
- Optional `playbooks/` — the NEXUS 7-phase lifecycle with phase
  runbooks, coordination docs, and scenario playbooks for
  startup-MVP, enterprise-feature, incident-response, and
  marketing-campaign workflows.
