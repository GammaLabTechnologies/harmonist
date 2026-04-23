#!/usr/bin/env python3
"""
telemetry_webhook.py -- forward local telemetry (counters + last-update
timestamp) to a webhook endpoint. Runs on demand (cron / CI / manual);
no always-on daemon. Strict opt-in: reads webhook URL from
`.cursor/hooks/config.json` or `$AGENT_PACK_TELEMETRY_WEBHOOK`, and
refuses to send anything when neither is set.

What it sends (JSON POST):

  {
    "schema":         "harmonist.telemetry/v1",
    "project":        "<project root basename>",
    "pack_version":   "<x.y.z>",
    "started_at":     "<first-telemetry timestamp>",
    "last_update_at": "<latest counter bump>",
    "summaries":      {...integer counters from agent-usage.json...},
    "agents":         {slug: {invocations, last_at}, ...},
    "delivered_at":   "<now, UTC>"
  }

What it does NOT send:

  - raw memory content
  - raw prompts or responses
  - per-user identifiers beyond the agent slug dashboard already in
    agent-usage.json (which is already local + .gitignored)

Usage:
    python3 harmonist/agents/scripts/telemetry_webhook.py
    python3 harmonist/agents/scripts/telemetry_webhook.py --project /p
    python3 harmonist/agents/scripts/telemetry_webhook.py --dry-run
    python3 harmonist/agents/scripts/telemetry_webhook.py --url https://...

Exit codes:
    0  delivered (or dry-run succeeded)
    1  webhook responded non-2xx
    2  no telemetry file or no webhook configured
"""

from __future__ import annotations

# === PY-GUARD:BEGIN ===
import sys as _asp_sys
if _asp_sys.version_info < (3, 9):
    _asp_cur = "%d.%d" % (_asp_sys.version_info[0], _asp_sys.version_info[1])
    _asp_sys.stderr.write(
        "harmonist requires Python 3.9+ (found " + _asp_cur + ").\n"
        "Install a modern Python and retry:\n"
        "  macOS:   brew install python@3.12 && hash -r\n"
        "  Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
        "  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0\n"
        "Then:     python3 " + _asp_sys.argv[0] + "\n"
    )
    _asp_sys.exit(3)
# === PY-GUARD:END ===

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


SCHEMA = "harmonist.telemetry/v1"


def _load_webhook_url(project: Path, cli_url: str | None) -> str:
    if cli_url:
        return cli_url.strip()
    env = os.environ.get("AGENT_PACK_TELEMETRY_WEBHOOK", "").strip()
    if env:
        return env
    cfg = project / ".cursor" / "hooks" / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            url = data.get("telemetry_webhook_url", "")
            if isinstance(url, str):
                return url.strip()
        except Exception:
            pass
    return ""


def _load_pack_version(project: Path) -> str:
    pv = project / ".cursor" / "pack-version.json"
    if not pv.exists():
        return ""
    try:
        return json.loads(pv.read_text()).get("pack_version", "")
    except Exception:
        return ""


def _load_telemetry(project: Path) -> dict:
    tel = project / ".cursor" / "telemetry" / "agent-usage.json"
    if not tel.exists():
        return {}
    try:
        return json.loads(tel.read_text())
    except Exception:
        return {}


def _post_json(url: str, payload: dict, timeout: int,
               extra_headers: dict[str, str] | None) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "harmonist-telemetry/1.0",
            **(extra_headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status, resp.read(2048).decode(
                "utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return (e.code, e.read(2048).decode("utf-8", errors="replace"))
    except urllib.error.URLError as e:
        return (0, f"URLError: {e.reason}")
    except Exception as e:
        return (0, f"{e.__class__.__name__}: {e}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--url", type=str, default=None,
                    help="Webhook URL (overrides config / env).")
    ap.add_argument("--header", action="append", default=[],
                    help="Extra HTTP header as 'Name: value'. Repeatable. "
                         "Use this for auth tokens (e.g. Slack bearer).")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the payload and print it; don't send.")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"telemetry_webhook: {project} is not a directory", file=sys.stderr)
        return 2

    webhook_url = _load_webhook_url(project, args.url)
    if not args.dry_run and not webhook_url:
        print("telemetry_webhook: no webhook URL configured. Set one of:\n"
              "  - CLI:        --url <url>\n"
              "  - env:        AGENT_PACK_TELEMETRY_WEBHOOK=<url>\n"
              "  - config:     .cursor/hooks/config.json "
              "{\"telemetry_webhook_url\": \"<url>\"}",
              file=sys.stderr)
        return 2

    tel = _load_telemetry(project)
    if not tel:
        print("telemetry_webhook: no telemetry file yet "
              f"({project}/.cursor/telemetry/agent-usage.json is missing)",
              file=sys.stderr)
        return 2

    payload = {
        "schema":         SCHEMA,
        "project":        project.name,
        "pack_version":   _load_pack_version(project),
        "started_at":     tel.get("started_at", ""),
        "last_update_at": tel.get("last_update_at", ""),
        "summaries":      tel.get("summaries", {}),
        "agents":         tel.get("agents", {}),
        "delivered_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    headers: dict[str, str] = {}
    for h in args.header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        print(f"\n(would POST to: {webhook_url or '<not configured>'})",
              file=sys.stderr)
        return 0

    status, body = _post_json(webhook_url, payload, args.timeout, headers)
    if 200 <= status < 300:
        print(f"  delivered ({status}): {len(payload['summaries'])} "
              f"summary counters, {len(payload['agents'])} agent slugs")
        return 0
    print(f"  FAIL: status={status}  body={body[:200]!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
