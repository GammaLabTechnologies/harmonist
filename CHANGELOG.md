# Changelog

All notable changes to Harmonist land here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Integrated projects record the pack version they adopted in
`.cursor/pack-version.json`. Use `agents/scripts/upgrade.py` to roll a
project forward; see the "Documentation" table in `README.md` for
what the upgrade tool is allowed to touch.

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
