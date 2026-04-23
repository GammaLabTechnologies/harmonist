# Pull request

<!-- Thanks for contributing. Keep the description focused on WHY, not just what. -->

## Summary

<!-- 1–3 sentences. What problem does this PR solve? -->

## Type of change

<!-- Check all that apply. -->

- [ ] Bug fix
- [ ] New feature / capability
- [ ] New agent added to the catalogue
- [ ] Existing agent updated
- [ ] Script / hook / memory change
- [ ] Documentation
- [ ] Refactor (no behaviour change)

## Related issue

<!-- Non-trivial work should link to an issue opened first. -->

Closes #

## Testing

<!-- How did you verify this doesn't regress anything? Paste test output. -->

```
# Example:
$ python3 agents/scripts/check_pack_health.py
Summary: 18/18 passed, 0 failure(s).

$ python3 agents/scripts/lint_agents.py
Linted 186 agent file(s): 0 error(s), 0 warning(s).
```

## Checklist

- [ ] Linter clean: `python3 agents/scripts/lint_agents.py`
- [ ] Pack healthy: `python3 agents/scripts/check_pack_health.py`
- [ ] Relevant `test_*.sh` scripts pass
- [ ] `agents/index.json` regenerated if agents changed
- [ ] `MANIFEST.sha256` regenerated if any tracked file changed
- [ ] `CHANGELOG.md` updated for user-visible changes
- [ ] Schema version bumped + migrator updated if schema changed
- [ ] No third-party Python dependencies added (stdlib only)
- [ ] For new agents: follows [`agents/STYLE.md`](../agents/STYLE.md)

## Breaking changes

<!-- If this changes any user-visible behaviour, describe the migration path. -->

None / describe:
