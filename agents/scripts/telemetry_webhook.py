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

Safety:
  - Only http/https destinations are allowed. A file://, ftp://, or other
    scheme is refused (a config/env-supplied URL must not be able to make
    urllib read a local file -- SSRF / local-file disclosure).
  - Redirects are NOT followed (a 30x Location could pivot to file:// or an
    internal host); a 3xx is treated as a delivery failure.
  - Transient failures (network errors, HTTP 429, and 5xx) are retried with
    exponential backoff + jitter, up to --attempts times (default 3).

Usage:
    python3 harmonist/agents/scripts/telemetry_webhook.py
    python3 harmonist/agents/scripts/telemetry_webhook.py --project /p
    python3 harmonist/agents/scripts/telemetry_webhook.py --dry-run
    python3 harmonist/agents/scripts/telemetry_webhook.py --url https://...
    python3 harmonist/agents/scripts/telemetry_webhook.py --attempts 5

Exit codes:
    0  delivered (or dry-run succeeded)
    1  webhook responded non-2xx (after retries)
    2  no telemetry file, no webhook configured, or disallowed URL scheme
"""

from __future__ import annotations

# === PY-GUARD:BEGIN ===
import sys as _asp_sys
if _asp_sys.version_info < (3, 9):
    _asp_cur = "%d.%d" % (_asp_sys.version_info[0], _asp_sys.version_info[1])
    # Guarded argv[0] FIRST: an empty argv (embedded interpreter) must get
    # the friendly message / JSON below, not an IndexError traceback.
    _asp_argv0 = _asp_sys.argv[0] if _asp_sys.argv else ""
    _asp_sys.stderr.write(
        "harmonist requires Python 3.9+ (found " + _asp_cur + ").\n"
        "Install a modern Python and retry:\n"
        "  macOS:   brew install python@3.12 && hash -r\n"
        "  Ubuntu:  sudo apt install python3.12 python3.12-venv\n"
        "  pyenv:   pyenv install 3.12.0 && pyenv local 3.12.0\n"
        "Then:     python3 " + _asp_argv0 + "\n"
    )
    # Cursor hooks read a JSON response from stdout; exiting without one
    # makes Cursor treat the hook as broken and silently drop the whole
    # enforcement layer -- including the fail-closed stop gate. When the
    # guarded script is the hook runner, answer the phase in-protocol
    # (shapes match hook_runner.py: emit_allow / "ask" / followup) and
    # exit 0 so the response is honoured. Every other script keeps the
    # plain exit(3).
    _asp_base = _asp_argv0.replace("\\", "/").split("/")[-1]
    if _asp_base == "hook_runner.py":
        _asp_phase = _asp_sys.argv[1] if len(_asp_sys.argv) > 1 else ""
        if _asp_phase == "beforeShellExecution":
            _asp_sys.stdout.write(
                '{"permission": "ask", "user_message": '
                '"harmonist hooks need Python 3.9+ (found ' + _asp_cur + '); '
                'the command safety gate cannot evaluate this command. '
                'Confirm it manually and upgrade python3."}\n'
            )
        elif _asp_phase == "stop":
            _asp_sys.stdout.write(
                '{"followup_message": '
                '"harmonist enforcement hooks need Python 3.9+ (found '
                + _asp_cur + ') and cannot verify the protocol gate '
                '(reviewers / session-handoff are NOT being checked). '
                'Upgrade python3 -- e.g. brew install python@3.12 or '
                'apt install python3.12 -- then retry."}\n'
            )
        else:
            _asp_sys.stdout.write("{}\n")
        _asp_sys.exit(0)
    _asp_sys.exit(3)
# === PY-GUARD:END ===

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SCHEMA = "harmonist.telemetry/v1"

# Only these URL schemes may be POSTed to. urllib.request.urlopen will happily
# open file://, ftp://, etc. -- a config- or env-supplied URL of file:///etc/...
# would read a LOCAL FILE (SSRF / local-file disclosure). Restrict to HTTP(S).
ALLOWED_SCHEMES = {"http", "https"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects. A 30x Location: could point at file:// or an
    internal address (SSRF pivot); surface the 3xx to the caller as a non-2xx
    instead of chasing it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_webhook_url(url: str) -> "tuple[bool, str]":
    if not url:
        return (False, "empty URL")
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as e:
        return (False, f"unparseable URL ({e.__class__.__name__})")
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return (False, f"scheme {parsed.scheme!r} not allowed "
                       f"(only http/https; got {url[:40]!r})")
    if not parsed.netloc:
        return (False, "URL has no host")
    return (True, "")


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
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return (status, resp.read(2048).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return (e.code, e.read(2048).decode("utf-8", errors="replace"))
    except urllib.error.URLError as e:
        return (0, f"URLError: {e.reason}")
    except Exception as e:
        return (0, f"{e.__class__.__name__}: {e}")


def _is_retryable(status: int) -> bool:
    """Transient failures worth retrying: network errors, rate-limit, 5xx.
    4xx (other than 429) are caller errors that a retry won't fix."""
    return status == 0 or status == 429 or 500 <= status < 600


def _post_with_retry(url: str, payload: dict, timeout: int,
                     headers: dict[str, str], attempts: int
                     ) -> "tuple[int, str]":
    """POST with exponential backoff + jitter, matching the pack's documented
    resilience policy (retries with backoff, bounded attempts)."""
    attempts = max(1, attempts)
    status, resp_body = 0, ""
    for i in range(attempts):
        status, resp_body = _post_json(url, payload, timeout, headers)
        if 200 <= status < 300:
            return (status, resp_body)
        if i < attempts - 1 and _is_retryable(status):
            delay = min(float(timeout), (2 ** i) * 0.5) + random.uniform(0, 0.5)
            print(f"  attempt {i + 1}/{attempts} failed (status={status}); "
                  f"retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
            continue
        break
    return (status, resp_body)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--url", type=str, default=None,
                    help="Webhook URL (overrides config / env).")
    ap.add_argument("--header", action="append", default=[],
                    help="Extra HTTP header as 'Name: value'. Repeatable. "
                         "Use this for auth tokens (e.g. Slack bearer).")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--attempts", type=int, default=3,
                    help="Max total send attempts (retries transient failures "
                         "-- network/429/5xx -- with exponential backoff + "
                         "jitter). Default 3.")
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

    # Refuse non-HTTP(S) destinations BEFORE building any request: a file://
    # or other-scheme URL would make urllib read a local resource.
    if webhook_url:
        ok, reason = _validate_webhook_url(webhook_url)
        if not ok:
            print(f"telemetry_webhook: refusing to use webhook URL -- {reason}",
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

    status, body = _post_with_retry(
        webhook_url, payload, args.timeout, headers, args.attempts)
    if 200 <= status < 300:
        print(f"  delivered ({status}): {len(payload['summaries'])} "
              f"summary counters, {len(payload['agents'])} agent slugs")
        return 0
    print(f"  FAIL: status={status}  body={body[:200]!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
