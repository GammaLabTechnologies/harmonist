#!/usr/bin/env bash
# beforeShellExecution hook (POSIX path).
#
# Human-in-the-loop gate on destructive / high-risk shell commands. We delegate
# to the Python runner's beforeShellExecution phase so the dangerous-command
# pattern list is single-sourced (hook_runner.py DEFAULT_CFG +
# .cursor/hooks/config.json overrides) and the POSIX and cross-platform paths
# behave identically. Without this, the POSIX hook set had NO dangerous-command
# protection at all.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$HERE/hook_runner.py" beforeShellExecution
fi

# No python3 available: we cannot evaluate the safety patterns, so ask for a
# human to confirm rather than silently allowing a possibly-destructive command.
printf '{"permission":"ask","user_message":"Command safety gate needs python3 to evaluate this command; confirm it is safe before running it."}\n'
