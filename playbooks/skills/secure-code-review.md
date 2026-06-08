# Secure Code Review

**Use when:** reviewing a diff or PR for security defects before it merges —
especially changes touching auth, secrets, payments, file/SSRF/network I/O,
deserialization, or access control.

## Method

1. **Scope the change.** Read the diff in full. Identify trust boundaries it
   crosses: user input, network, filesystem, DB, auth/session, secrets.
2. **Input handling.** For every new input path, check: validation,
   parameterization (no string-built SQL/commands/queries), encoding/escaping
   at the sink, size/shape limits.
3. **Authn / authz.** Does the change add an endpoint or action? Confirm it
   checks identity AND authorization (object-level too — IDOR), not just
   "logged in". Default-deny.
4. **Secrets & data.** No secrets in code, logs, or errors. PII/sensitive data
   is minimized, encrypted in transit, and not over-returned.
5. **Dangerous sinks.** Flag `eval`/dynamic exec, deserialization of untrusted
   data, SSRF-prone outbound calls, path joins from user input, unsafe file
   uploads.
6. **Dependencies & config.** New deps vetted? New config/feature flags fail
   safe? Crypto uses vetted primitives (no home-rolled).
7. **Confirm, don't assume.** For anything you flag, cite the exact line and
   the concrete exploit/impact — not a vague worry.

## Output

- verdict: pass | changes-requested | block
- findings: each with file:line, the issue, a concrete exploit/impact, and the
  fix
- positive_notes: security-relevant things done correctly (so they're kept)
- tests_to_add: the cases that would catch a regression of each finding

## Guardrails

- This is a *review* skill — read-only. Don't edit; hand fixes back.
- Severity reflects real exploitability + impact in this system's context
  (read the project `AGENTS.md` Invariants), not a generic checklist score.
