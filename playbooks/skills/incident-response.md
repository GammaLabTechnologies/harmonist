# Incident Response

**Use when:** a production incident is active (outage, regression, security
event) and needs structured triage → mitigation → resolution → learning.

## Method

1. **Declare & stabilize.** State severity, impact (who/what is affected), and
   the current hypothesis in one line. Stop the bleeding FIRST (mitigate /
   roll back / feature-flag off) before root-causing — restore service, then
   investigate.
2. **Establish a timeline.** What changed recently (deploys, config, traffic,
   dependencies)? Correlate with when symptoms began. The repo map's
   `impact`/`affected` and recent commits narrow the suspect set fast.
3. **Localize.** Form a falsifiable hypothesis, test it with evidence (logs,
   metrics, traces, a minimal repro), and narrow to the responsible
   change/component. Don't guess-fix.
4. **Remediate.** Apply the smallest correct fix. For data/migrations, prefer
   a forward/compensating change over editing history. Verify recovery with
   the same signal that showed the failure.
5. **Confirm & close.** Confirm metrics are back to baseline; remove temporary
   mitigations deliberately.
6. **Learn.** Write a blameless post-mortem: timeline, root cause, why it
   wasn't caught, and concrete prevention (a test, an alert, a guardrail).

## Output

- severity, impact, and timeline
- root_cause: the actual cause, with evidence
- mitigation_applied and how recovery was verified
- follow_ups: the prevention items (each an owned, concrete action)

## Guardrails

- Service restoration beats elegance — mitigate before root-cause when users
  are affected.
- Record actions as you take them; under pressure, memory is unreliable.
- Destructive recovery steps (data deletes, force operations) get a second
  set of eyes — the HITL gate will pause them.
