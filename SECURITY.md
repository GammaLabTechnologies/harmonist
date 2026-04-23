# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `1.x`   | ✅        |
| `< 1.0` | ❌        |

Security fixes are backported only to the latest `1.x` minor release.

## Reporting a vulnerability

**Do not open a public GitHub issue** for security reports.

Open a **private security advisory** via the repository's **Security** tab
(`Security` → `Report a vulnerability`). Provide:

- A clear description of the problem and the attack surface.
- Reproduction steps or a minimal proof-of-concept.
- The affected pack version (`VERSION` file or
  `.cursor/pack-version.json`).
- Your assessment of severity (critical / high / medium / low).

Encrypted communication is available on request.

## Response timeline

| Stage | Target |
|-------|--------|
| Acknowledgment | within 48 hours |
| Initial triage | within 7 days |
| Fix or mitigation | depends on severity (critical: ≤ 14 days; others: next minor) |
| Public disclosure | coordinated with the reporter |

## Scope

Harmonist ships two classes of assets, both in-scope for security review:

**Shipped code — always in scope:**

- `agents/scripts/*.py` + `hooks/scripts/*.{py,sh}` — the enforcement
  layer. Any path traversal, command injection, or privilege escalation
  vector is a security bug.
- `memory/memory.py` + `memory/validate.py` — the supply-chain-sensitive
  CLI and validator. Bypassing secret-pattern scans or validator checks
  counts as in-scope.
- `MANIFEST.sha256` verification in `upgrade.py` / `install_extras.py` —
  any way to install a tampered source file is critical.
- `scan_agent_safety.py` — the prompt-injection scanner. False negatives
  on documented hostile patterns are in-scope.

**Shipped prompts (agent `.md` files) — limited scope:**

- Hostile content in agent bodies that tries to override the protocol,
  exfiltrate secrets, or subvert review gates is a bug. `scan_agent_safety.py`
  is expected to catch these on the catalogue we ship; escapes are in-scope.
- Prompts misbehaving under adversarial user input (jailbreak research)
  are **out of scope** — no LLM prompt is bulletproof.

**Out of scope:**

- LLM hallucinations or model-specific behaviour.
- Issues in third-party tools (Cursor, Claude Code, Copilot, etc.).
- Social-engineering attacks against maintainers.

## Hardening guidance for users

- Run `python3 agents/scripts/check_pack_health.py` after every pull.
  It verifies `MANIFEST.sha256`, lints the catalogue, and runs the
  prompt-injection scanner.
- Keep `.cursor/memory/*.md` in `.gitignore` unless the project is
  public and the content has been reviewed. Memory files accumulate
  project-sensitive state.
- Set `telemetry_enabled: false` in `.cursor/hooks/config.json` if local
  agent-usage counters are undesirable.
- When upgrading a project, always use `upgrade.py --apply` (not manual
  copy). `--apply` sha-verifies every source file before it touches the
  project.
