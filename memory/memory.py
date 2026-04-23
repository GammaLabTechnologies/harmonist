#!/usr/bin/env python3
"""
memory.py — the one supported path for appending to .cursor/memory/*.md.

Why a CLI?
  * Generates `id`, `correlation_id`, and `at` automatically so the LLM
    cannot lie about them.
  * Reads the active correlation ID from the hooks' state file. If the
    hooks are not running (e.g. manual invocation outside Cursor), falls
    back to a fresh ID derived from the current time.
  * Validates the entry before appending so no broken block ever lands.

Usage:
  memory.py append --file session-handoff --kind state --status done \
                   --summary "..." [--body-file body.md | --body "..."] \
                   [--tags a,b,c] [--author orchestrator]

  memory.py show <id>
  memory.py list --file <file> [--kind k] [--correlation cid]
  memory.py latest --file <file> --kind <kind> [--n N]
  memory.py validate [--path <dir>] [--strict]
  memory.py current-id              # print the active correlation_id
  memory.py bump-task               # increment task_seq (hooks use this)
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
import re
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Secret patterns -- refuse to append an entry whose body looks like it
# contains raw credentials. Override with --allow-secrets when you really
# know what you are doing.
#
# Patterns are deliberately narrow enough to avoid false positives on
# placeholder text like <PLACEHOLDER> or `${STRIPE_KEY}`. Expand only when
# a genuine leak passes through.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key id",   re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret key",      re.compile(r"\b[0-9a-zA-Z/+]{40}\b(?=.*aws)", re.IGNORECASE)),
    ("GitHub PAT",          re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("GitHub OAuth",        re.compile(r"\bgho_[A-Za-z0-9]{36}\b")),
    ("GitHub app token",    re.compile(r"\bghs_[A-Za-z0-9]{36}\b")),
    ("GitLab PAT",          re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI API key",      re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Anthropic API key",   re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("Stripe secret",       re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{20,}\b")),
    ("Slack token",         re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Slack webhook",       re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}")),
    ("JWT (eyJ...)",        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("private key PEM",     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("TON mnemonic hint",   re.compile(r"(?:\b[a-z]{4,8}\b\s+){23}\b[a-z]{4,8}\b")),
    # Google / GCP
    ("Google API key",      re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Google OAuth token",  re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}\b")),
    ("GCP service account", re.compile(r"\"type\"\s*:\s*\"service_account\"")),
    # Azure
    ("Azure conn-string",   re.compile(
        r"DefaultEndpointsProtocol=https;AccountName=[A-Za-z0-9]+;AccountKey=[A-Za-z0-9+/=]{40,}",
    )),
    ("Azure SAS token",     re.compile(r"\bsig=[A-Za-z0-9%]{40,}&se=\d{4}-\d{2}-\d{2}")),
    # Twilio
    ("Twilio account SID",  re.compile(r"\bAC[a-f0-9]{32}\b")),
    ("Twilio API key SID",  re.compile(r"\bSK[a-f0-9]{32}\b")),
    # Messaging / collaboration
    ("Discord bot token",   re.compile(r"\b[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}\b")),
    ("Discord webhook",     re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d{17,}/[\w-]{60,}")),
    ("Telegram bot token",  re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b")),
    # Mail
    ("SendGrid API key",    re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
    ("Mailgun key",         re.compile(r"\bkey-[a-f0-9]{32}\b")),
    # Cloud / infra
    ("DigitalOcean PAT",    re.compile(r"\bdop_v1_[a-f0-9]{64}\b")),
    ("npm token",           re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("PyPI token",          re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,}\b")),
    ("Docker Hub token",    re.compile(r"\bdckr_pat_[A-Za-z0-9_\-]{27,}\b")),
    # Generic DSN with credentials  (Postgres / MySQL / Mongo / Redis over https-ish)
    ("DB URL with creds",   re.compile(r"\b(?:postgres|postgresql|mysql|mongodb|mongodb\+srv|redis|rediss|amqp|amqps)://[^\s:/@]+:[^\s:/@]{4,}@[^\s/]+")),
]

# Context-scoped patterns -- a UUID is only a credential when it sits
# near a known vendor name. We scan for the vendor word and then look
# for a UUID within +/- 60 chars. This catches "heroku api key: <UUID>"
# AND "<UUID> # postmark server token" without false-positive-flagging
# every UUID in the body.
_UUID_RE = re.compile(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b")
_CONTEXT_VENDORS: list[tuple[str, re.Pattern]] = [
    ("Heroku API key",       re.compile(r"(?i)\bheroku\b")),
    ("Postmark server token", re.compile(r"(?i)\bpostmark\b")),
]


def _scan_context_uuids(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for name, vendor_rx in _CONTEXT_VENDORS:
        for v_match in vendor_rx.finditer(text):
            # Window of +/- 60 chars around the vendor mention.
            lo = max(0, v_match.start() - 60)
            hi = min(len(text), v_match.end() + 60)
            window = text[lo:hi]
            u_match = _UUID_RE.search(window)
            if u_match:
                sample = u_match.group(0)
                if len(sample) > 30:
                    sample = sample[:27] + "..."
                hits.append((name, sample))
                break
    return hits


# Fences around likely-placeholder text. If the matched substring sits
# entirely inside angle-brackets / $(...) / ${...} / <...> / ALL_CAPS
# envvar references, skip -- it is almost certainly a placeholder.
_PLACEHOLDER_FENCES = [
    re.compile(r"<[^<>]*>"),                    # <PLACEHOLDER>
    re.compile(r"\$\{[^{}]*\}"),                # ${VAR}
    re.compile(r"\$\([^()]*\)"),                # $(cmd) / $(VAR)
    re.compile(r"\{\{[^{}]*\}\}"),              # {{ template }}
    re.compile(r"%%[A-Z0-9_]+%%"),              # %%PLACEHOLDER%%
]


def _looks_like_placeholder(text: str, start: int, end: int) -> bool:
    """Return True if the match [start:end] lives inside a placeholder
    fence (e.g. <TOKEN>, ${VAR}, $(cmd), {{ var }}, %%X%%)."""
    for fence in _PLACEHOLDER_FENCES:
        for m in fence.finditer(text):
            if m.start() <= start and end <= m.end():
                return True
    return False


def _shannon_entropy(s: str) -> float:
    """Shannon entropy in bits/char. Used as a last-line generic-token
    detector. Random base64 / hex blobs score > 4.0; English prose
    scores ~3.5-4.0; URLs / hex hashes score 3.0-4.5."""
    from collections import Counter
    from math import log2
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * log2(c / length) for c in counts.values())


# Generic high-entropy token heuristic: a bare word of 28+ chars from
# the typical token alphabet with entropy >= 4.3 bits/char. Tuned to
# fire on real random tokens while staying quiet on file paths, URLs,
# SHA-256 hashes (entropy ~4.0), and prose.
_HIGH_ENTROPY_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{28,}\b")
_HIGH_ENTROPY_THRESHOLD = 4.3
# Keys that clearly introduce a secret value. If one of these appears
# within ~40 chars before the high-entropy token, treat the token as a
# secret even when its entropy alone is borderline.
_SECRET_CONTEXT_PREFIX = re.compile(
    r"(?i)\b(?:secret|password|passwd|api[_-]?key|access[_-]?key|"
    r"auth[_-]?token|bearer|private[_-]?key)\b\s*[:=]\s*['\"]?$"
)


def _scan_generic_tokens(text: str) -> list[tuple[str, str]]:
    """Flag bare high-entropy tokens that no pattern above caught.
    Skips placeholders, markdown code-fence hashes, and obvious
    file-path / URL substrings."""
    hits: list[tuple[str, str]] = []
    for m in _HIGH_ENTROPY_TOKEN.finditer(text):
        token = m.group(0)
        start, end = m.start(), m.end()
        if _looks_like_placeholder(text, start, end):
            continue
        # Skip if surrounded by / ? = & as part of a normal URL path,
        # unless a secret-context prefix sits right in front.
        prefix = text[max(0, start - 40):start]
        ent = _shannon_entropy(token)
        if _SECRET_CONTEXT_PREFIX.search(prefix):
            # Context forces a hit even at moderate entropy.
            if ent >= 3.8:
                sample = token[:27] + "..." if len(token) > 30 else token
                hits.append((f"secret-context token (entropy {ent:.2f})", sample))
            continue
        if ent < _HIGH_ENTROPY_THRESHOLD:
            continue
        # Avoid hash-looking nonsense in URLs / file paths.
        if any(ch in prefix[-3:] for ch in ("/", "?", "&", "=")) and "secret" not in prefix.lower():
            continue
        sample = token[:27] + "..." if len(token) > 30 else token
        hits.append((f"high-entropy token (entropy {ent:.2f})", sample))
    return hits


def scan_for_secrets(text: str) -> list[tuple[str, str]]:
    """Return [(pattern_name, first-30-char-sample), ...] for every hit.

    Runs each explicit pattern, then falls back to the generic
    high-entropy detector for bare tokens no pattern covered."""
    hits: list[tuple[str, str]] = []
    seen_spans: list[tuple[int, int]] = []
    for name, pat in _SECRET_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if _looks_like_placeholder(text, m.start(), m.end()):
            continue
        sample = m.group(0)
        if len(sample) > 30:
            sample = sample[:27] + "..."
        hits.append((name, sample))
        seen_spans.append((m.start(), m.end()))
    # Context-scoped vendor + UUID pairs.
    for name, sample in _scan_context_uuids(text):
        hits.append((name, sample))
    # Generic entropy pass -- deduplicate against explicit hits.
    for name, sample in _scan_generic_tokens(text):
        # If this token overlaps a specific hit, skip.
        m = re.search(re.escape(sample.rstrip(".")[:20]), text)
        if m and any(s <= m.start() and m.end() <= e for s, e in seen_spans):
            continue
        hits.append((name, sample))
    return hits

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from validate import (  # noqa: E402
    FILE_KIND,
    VALID_AUTHORS,
    VALID_KINDS,
    VALID_STATUSES,
    discover_files,
    iter_entries,
    Report,
    validate,
)


# --------------------------------------------------------------------------- locating things


def memory_dir() -> Path:
    """Directory holding session-handoff.md / decisions.md / patterns.md."""
    # When run as `.cursor/memory/memory.py` the dir containing it IS the memory dir.
    # When run from the pack templates, we're in pack/memory/.
    return SCRIPT_DIR


def hooks_state_path() -> Path:
    """Locate the hooks' session.json state file.

    Preference order:
      1. $AGENT_PACK_HOOKS_STATE — explicit override (used by tests and by
         setups where hooks and memory are not co-located).
      2. `.cursor/hooks/.state/session.json` walking up from the CLI's
         own directory (the normal post-integration layout).
      3. `<pack>/hooks/.state/session.json` (pack-development mode).
      4. `<memory-dir>/.state/session.json` — standalone fallback so the
         CLI still works when Cursor hooks are not installed at all.
    """
    env = os.environ.get("AGENT_PACK_HOOKS_STATE")
    if env:
        return Path(env)
    cur = SCRIPT_DIR.resolve()
    for _ in range(6):
        candidate = cur / ".cursor" / "hooks" / ".state" / "session.json"
        if candidate.exists():
            return candidate
        cur = cur.parent
    pack_hooks = SCRIPT_DIR.parent / "hooks" / ".state" / "session.json"
    if pack_hooks.exists():
        return pack_hooks
    return SCRIPT_DIR / ".state" / "session.json"


# --------------------------------------------------------------------------- state handling


def _load_state() -> dict:
    path = hooks_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    path = hooks_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def active_correlation_id() -> str:
    """Return the current <session_id>-<task_seq> correlation id.

    Prefers the one set by the enforcement hooks. If none exists, generates
    a fresh session and task_seq=0 and persists it to the fallback state.
    """
    state = _load_state()
    cid = state.get("active_correlation_id")
    if cid:
        return cid
    # bootstrap -- session_id = <unix-seconds><pid4> matches the hook
    # bootstrap format (lib.sh::_state_bootstrap) so the CLI and the
    # hooks agree on shape and never clash when both happen to
    # bootstrap in the same second.
    session_id = state.get("session_id") or f"{int(time.time())}{os.getpid() % 10000:04d}"
    task_seq = int(state.get("task_seq", 0))
    cid = f"{session_id}-{task_seq}"
    state.setdefault("session_id", session_id)
    state.setdefault("task_seq", task_seq)
    state["active_correlation_id"] = cid
    _save_state(state)
    return cid


def bump_task() -> str:
    """Advance task_seq by one and return the new active_correlation_id.

    Called by the stop hook after a successful, validated completion.
    """
    state = _load_state()
    session_id = state.get("session_id") or f"{int(time.time())}{os.getpid() % 10000:04d}"
    task_seq = int(state.get("task_seq", 0)) + 1
    cid = f"{session_id}-{task_seq}"
    state["session_id"] = session_id
    state["task_seq"] = task_seq
    state["active_correlation_id"] = cid
    _save_state(state)
    return cid


# --------------------------------------------------------------------------- entry construction


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _render_entry(
    *, id_: str, correlation_id: str, kind: str, status: str, author: str,
    summary: str, body: str, tags: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    # Imported here to avoid a circular at module load.
    from validate import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
    lines = [
        "<!-- memory-entry:start -->",
        "---",
        f"schema_version: {MEMORY_SCHEMA_VERSION}",
        f"id: {id_}",
        f"correlation_id: {correlation_id}",
        f"at: {_iso_now()}",
        f"kind: {kind}",
        f"status: {status}",
        f"author: {author}",
        f"summary: {summary}",
    ]
    if tags:
        lines.append(f"tags: [{', '.join(sorted(set(tags)))}]")
    if extra:
        for k, v in extra.items():
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body.rstrip())
    lines.append("")
    lines.append("<!-- memory-entry:end -->")
    return "\n".join(lines) + "\n"


def _append_block(file: Path, block: str) -> None:
    existing = file.read_text() if file.exists() else ""
    if existing and not existing.endswith("\n\n"):
        sep = "\n" if existing.endswith("\n") else "\n\n"
    else:
        sep = ""
    file.write_text(existing + sep + block)


# --------------------------------------------------------------------------- subcommands


def cmd_append(args: argparse.Namespace) -> int:
    kind = args.kind
    if kind not in VALID_KINDS:
        print(f"kind must be one of {sorted(VALID_KINDS)}", file=sys.stderr)
        return 2
    if args.status not in VALID_STATUSES:
        print(f"status must be one of {sorted(VALID_STATUSES)}", file=sys.stderr)
        return 2
    if args.author not in VALID_AUTHORS:
        print(f"author must be one of {sorted(VALID_AUTHORS)}", file=sys.stderr)
        return 2

    file = args.file
    if not file.endswith(".md"):
        file = f"{file}.md"
    file_path = memory_dir() / file
    # Check kind matches file expectation (if standard file)
    expected_kind = FILE_KIND.get(file_path.name)
    if expected_kind is None:
        # maybe *.shared.md
        for base, k in FILE_KIND.items():
            bare = base.removesuffix(".md")
            if file_path.name == base or file_path.name.startswith(bare + "."):
                expected_kind = k
                break
    if expected_kind and kind != expected_kind:
        print(
            f"error: file {file_path.name} requires kind={expected_kind!r}, got {kind!r}",
            file=sys.stderr,
        )
        return 2

    body = ""
    if args.body_file:
        body = Path(args.body_file).read_text()
    elif args.body:
        body = args.body
    else:
        # Read from stdin
        body = sys.stdin.read()
    body = body.strip()
    if not body:
        print("error: body is empty (use --body, --body-file, or pipe to stdin)", file=sys.stderr)
        return 2

    # Secret scan. Memory files WILL end up checked into someone's repo by
    # accident (or shared via *.shared.md on purpose), so refuse the append
    # if the body or summary contains a recognisable credential shape.
    combined = f"{args.summary}\n{body}"
    hits = scan_for_secrets(combined)
    if hits and not getattr(args, "allow_secrets", False):
        print("error: refusing to append -- body looks like it contains secrets:",
              file=sys.stderr)
        for name, sample in hits:
            print(f"  * {name}: {sample}", file=sys.stderr)
        print("Replace the values with <PLACEHOLDERS>. If this is a false "
              "positive, re-run with --allow-secrets.", file=sys.stderr)
        return 2

    cid = active_correlation_id()
    # Build an id that uniquely disambiguates repeats in the same task.
    base_id = f"{cid}-{kind}"
    existing_ids = _collect_ids(file_path.parent if file_path.parent.exists() else memory_dir())
    id_ = base_id
    n = 1
    while id_ in existing_ids:
        n += 1
        id_ = f"{base_id}-{n}"

    tags = []
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    extra = {}
    if args.scope:
        extra["scope"] = args.scope
    if args.author_detail:
        extra["author_detail"] = args.author_detail
    if args.links:
        extra["links"] = [l.strip() for l in args.links.split(",") if l.strip()]

    block = _render_entry(
        id_=id_,
        correlation_id=cid,
        kind=kind,
        status=args.status,
        author=args.author,
        summary=args.summary.strip(),
        body=body,
        tags=tags,
        extra=extra,
    )

    # Dedupe guard. Refuses any append whose `summary` matches an
    # existing entry ANYWHERE in the file (not just the last 10 --
    # that earlier window could be bypassed by inserting 10 filler
    # entries between the banned summary and the re-emit).
    #
    # Also refuses body-hash collisions with any existing entry, so a
    # re-worded summary on top of an unchanged body is still caught.
    #
    # Scanning is cheap: each file is typically < 200 entries and body
    # hashing uses a short blake2s digest.
    if file_path.exists() and not getattr(args, "allow_duplicate", False):
        import hashlib
        dup_summary = args.summary.strip()
        incoming_hash = hashlib.blake2s(body.encode("utf-8"), digest_size=8).hexdigest()
        recent_report = Report()
        all_entries = list(iter_entries(file_path, recent_report))
        for e in all_entries:
            prev_summary = str(e.frontmatter.get("summary", "")).strip()
            prev_body = (e.body or "").strip()
            prev_hash = hashlib.blake2s(prev_body.encode("utf-8"), digest_size=8).hexdigest()
            dup_id = e.frontmatter.get("id", "?")
            if prev_summary and prev_summary == dup_summary:
                print(
                    f"error: {file_path.name} already has the same summary:\n"
                    f"  id: {dup_id}\n"
                    f"  summary: {prev_summary}\n"
                    "If this is an intentional re-emit (e.g. state snapshot "
                    "that hasn't changed), re-run with --allow-duplicate. "
                    "Otherwise rewrite the summary to name what changed.",
                    file=sys.stderr,
                )
                return 2
            if prev_hash == incoming_hash and prev_body:
                print(
                    f"error: {file_path.name} already has an entry with the "
                    f"same body (hash match):\n"
                    f"  id: {dup_id}\n"
                    f"  prev summary: {prev_summary}\n"
                    f"  new  summary: {dup_summary}\n"
                    "The body is byte-identical to an existing entry; the "
                    "summary was just re-worded. Re-run with "
                    "--allow-duplicate if this is intentional.",
                    file=sys.stderr,
                )
                return 2

    # Dry-run path: just print the block.
    if args.dry_run:
        sys.stdout.write(block)
        return 0

    file_path.parent.mkdir(parents=True, exist_ok=True)
    _append_block(file_path, block)

    # Validate the freshly-written file. Rollback on failure.
    report = validate([file_path])
    if report.errors:
        # Revert by removing the block we just added.
        current = file_path.read_text()
        if current.endswith(block):
            file_path.write_text(current[: -len(block)].rstrip("\n") + "\n")
        for err in report.errors:
            print(f"ERROR {err}", file=sys.stderr)
        print(
            f"error: the rendered block failed schema validation; rolled back {file_path}",
            file=sys.stderr,
        )
        return 1

    print(id_)
    return 0


def _collect_ids(base: Path) -> set[str]:
    ids: set[str] = set()
    for f in discover_files(base):
        report = Report()
        for e in iter_entries(f, report):
            eid = e.frontmatter.get("id")
            if eid:
                ids.add(str(eid))
    return ids


def cmd_show(args: argparse.Namespace) -> int:
    target_id = args.id
    for f in discover_files(memory_dir()):
        report = Report()
        for e in iter_entries(f, report):
            if str(e.frontmatter.get("id", "")) == target_id:
                print(f"# {e.file.name} (lines {e.line_start}-{e.line_end})")
                for k, v in e.frontmatter.items():
                    print(f"{k}: {v}")
                print()
                print(e.body)
                return 0
    print(f"no entry found with id={target_id}", file=sys.stderr)
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    file = args.file
    if not file.endswith(".md"):
        file = f"{file}.md"
    path = memory_dir() / file
    report = Report()
    for e in iter_entries(path, report):
        if args.kind and e.frontmatter.get("kind") != args.kind:
            continue
        if args.correlation and e.frontmatter.get("correlation_id") != args.correlation:
            continue
        print(
            f"{e.frontmatter.get('id','?'):40s}  "
            f"{e.frontmatter.get('at','?'):21s}  "
            f"{e.frontmatter.get('status','?'):12s}  "
            f"{e.frontmatter.get('summary','')}"
        )
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    file = args.file
    if not file.endswith(".md"):
        file = f"{file}.md"
    path = memory_dir() / file
    report = Report()
    entries = [e for e in iter_entries(path, report) if e.frontmatter.get("kind") == args.kind]
    for e in entries[-max(args.n, 1):]:
        sys.stdout.write(f"<!-- memory-entry:start -->\n---\n")
        for k, v in e.frontmatter.items():
            if isinstance(v, list):
                sys.stdout.write(f"{k}: [{', '.join(v)}]\n")
            else:
                sys.stdout.write(f"{k}: {v}\n")
        sys.stdout.write("---\n\n")
        sys.stdout.write(e.body + "\n\n<!-- memory-entry:end -->\n\n")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search across every memory file. Supports text substring (--query),
    tag filter (--tag), kind filter (--kind), status filter (--status),
    and time window via --since / --until (YYYY-MM-DD).

    Matches are scored coarsely: a text hit in `summary` ranks higher
    than a body-only hit. Output is tab-separated (id, at, file, kind,
    status, summary) for easy piping to `awk` / `cut`; --json emits
    structured results."""
    import datetime as dt

    query = (args.query or "").lower().strip()
    want_tag = (args.tag or "").strip()
    want_kind = (args.kind or "").strip()
    want_status = (args.status or "").strip()
    want_author = (args.author or "").strip()

    def _parse_iso(s: str) -> dt.date | None:
        try:
            return dt.date.fromisoformat(s[:10])
        except Exception:
            return None

    since = _parse_iso(args.since) if args.since else None
    until = _parse_iso(args.until) if args.until else None

    files = (
        [memory_dir() / (args.file if args.file.endswith(".md") else args.file + ".md")]
        if args.file else discover_files(memory_dir())
    )

    hits: list[dict] = []
    for f in files:
        if not f.exists():
            continue
        report = Report()
        for e in iter_entries(f, report):
            fm = e.frontmatter
            summary = str(fm.get("summary", ""))
            body = e.body or ""
            if want_kind and fm.get("kind") != want_kind:
                continue
            if want_status and fm.get("status") != want_status:
                continue
            if want_author and want_author.lower() not in str(fm.get("author", "")).lower():
                continue
            if want_tag:
                tags = fm.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.strip("[]").split(",")]
                if want_tag not in [str(t).strip() for t in tags]:
                    continue
            at = str(fm.get("at", ""))
            at_date = _parse_iso(at)
            if since and (at_date is None or at_date < since):
                continue
            if until and (at_date is None or at_date > until):
                continue

            summary_hit = query and query in summary.lower()
            body_hit = query and query in body.lower()
            if query and not (summary_hit or body_hit):
                continue
            score = (2 if summary_hit else 0) + (1 if body_hit else 0)

            hits.append({
                "id":      str(fm.get("id", "")),
                "at":      at,
                "file":    f.name,
                "kind":    str(fm.get("kind", "")),
                "status":  str(fm.get("status", "")),
                "summary": summary[:200],
                "tags":    list(fm.get("tags") or []),
                "score":   score,
            })

    # Rank: higher score first; within equal score, newer `at` first.
    # Python's sort is stable, so sorting by the secondary key first and
    # the primary key second produces the expected (score desc, at desc)
    # ordering in one pass plus one stable re-sort.
    hits.sort(key=lambda h: h["at"], reverse=True)
    hits.sort(key=lambda h: -h["score"])

    limit = max(args.limit, 1)
    hits = hits[:limit]

    if args.json:
        print(json.dumps({"hits": hits, "count": len(hits)}, indent=2))
        return 0 if hits else 1

    if not hits:
        print("no matches", file=sys.stderr)
        return 1
    for h in hits:
        print("\t".join([
            h["id"], h["at"], h["file"], h["kind"], h["status"], h["summary"],
        ]))
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    """Rotate the named memory file: move entries older than --keep-last
    (by count) to an archive file with an ISO-date suffix, optionally
    keeping only entries newer than --since. Active file continues to
    exist with the most recent entries so readers (session-start
    bootstrap) don't churn through thousands of lines.

    This is lossless: archive file carries the same schema, same
    markers, same validation. Rotation is refused if the --keep-last
    would leave fewer than 1 entry."""
    import datetime as dt

    file = args.file
    if not file.endswith(".md"):
        file = f"{file}.md"
    path = memory_dir() / file
    if not path.exists():
        print(f"no such memory file: {path}", file=sys.stderr)
        return 1

    report = Report()
    entries = list(iter_entries(path, report))
    if report.errors and not args.force:
        for e in report.errors:
            print(f"ERROR {e}", file=sys.stderr)
        print("refusing to rotate a file with validation errors (use --force)",
              file=sys.stderr)
        return 2

    keep_last = args.keep_last
    since_date = None
    if args.since:
        try:
            since_date = dt.date.fromisoformat(args.since[:10])
        except Exception:
            print(f"--since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 2

    def _keep(e) -> bool:
        if since_date is not None:
            at = str(e.frontmatter.get("at", ""))[:10]
            try:
                return dt.date.fromisoformat(at) >= since_date
            except Exception:
                return False
        return False

    # Pick entries to KEEP in the live file.
    if since_date is not None:
        kept = [e for e in entries if _keep(e)]
        archived = [e for e in entries if not _keep(e)]
    else:
        if keep_last is None or keep_last <= 0:
            print("--keep-last must be a positive integer when --since is absent",
                  file=sys.stderr)
            return 2
        kept = entries[-keep_last:]
        archived = entries[:-keep_last] if keep_last < len(entries) else []

    if not kept:
        print("rotate would leave the live file with zero entries; refusing",
              file=sys.stderr)
        return 2
    if not archived:
        print("nothing to rotate (archive would be empty)")
        return 0

    def _render(entries_: list) -> str:
        out: list[str] = []
        for e in entries_:
            out.append("<!-- memory-entry:start -->")
            out.append("---")
            for k, v in e.frontmatter.items():
                if isinstance(v, list):
                    out.append(f"{k}: [{', '.join(str(x) for x in v)}]")
                else:
                    out.append(f"{k}: {v}")
            out.append("---")
            out.append("")
            out.append(e.body)
            out.append("")
            out.append("<!-- memory-entry:end -->")
            out.append("")
        return "\n".join(out) + "\n"

    # Archive name: <stem>-archive-<YYYY-MM-DD>.md
    today = dt.date.today().isoformat()
    stem = path.stem
    archive = path.with_name(f"{stem}-archive-{today}.md")

    # If archive already exists, append to it.
    archive_prev = archive.read_text() if archive.exists() else ""
    archive_block = _render(archived)
    # File-level header: preserve the ORIGINAL header from the live file
    # on the archive's first write.
    if not archive_prev:
        # Pick everything before the first `<!-- memory-entry:start -->`
        # from the live file as the header.
        live_text = path.read_text()
        cut = live_text.find("<!-- memory-entry:start -->")
        header = live_text[:cut] if cut >= 0 else ""
        archive_prev = header
        if not archive_prev.endswith("\n"):
            archive_prev += "\n"

    if args.dry_run:
        print(f"would rotate {len(archived)} entry/ies into {archive.name}")
        print(f"live file would keep {len(kept)} entry/ies")
        return 0

    archive.write_text(archive_prev + archive_block)

    # Rewrite live file: preserve header (everything before first entry),
    # then emit the kept entries.
    live_text = path.read_text()
    cut = live_text.find("<!-- memory-entry:start -->")
    header = live_text[:cut] if cut >= 0 else ""
    path.write_text(header + _render(kept))

    # Re-validate both outputs.
    v_live = validate([path])
    v_arch = validate([archive])
    if v_live.errors or v_arch.errors:
        for e in v_live.errors + v_arch.errors:
            print(f"ERROR {e}", file=sys.stderr)
        print("rotate produced invalid output (see errors above)", file=sys.stderr)
        return 1

    print(f"rotated {len(archived)} entry/ies -> {archive.name}; "
          f"live file kept {len(kept)} entry/ies")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if args.path:
        files = discover_files(args.path)
    else:
        files = discover_files(memory_dir())
    report = validate(files, strict=args.strict)
    for w in report.warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in report.errors:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if report.errors else 0


def cmd_current_id(_: argparse.Namespace) -> int:
    print(active_correlation_id())
    return 0


def cmd_bump_task(_: argparse.Namespace) -> int:
    print(bump_task())
    return 0


# --------------------------------------------------------------------------- argparse wiring


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append a new entry")
    a.add_argument("--file", required=True, help="session-handoff | decisions | patterns (or filename)")
    a.add_argument("--kind", required=True, choices=sorted(VALID_KINDS))
    a.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    a.add_argument("--author", default="orchestrator", choices=sorted(VALID_AUTHORS))
    a.add_argument("--summary", required=True)
    a.add_argument("--body")
    a.add_argument("--body-file")
    a.add_argument("--tags", help="Comma-separated list")
    a.add_argument("--scope")
    a.add_argument("--author-detail")
    a.add_argument("--links", help="Comma-separated list of entry ids")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--allow-secrets", action="store_true",
                   help="Override the secret-pattern guard. Only use when the "
                        "match is a false positive; never to commit real creds.")
    a.add_argument("--allow-duplicate", action="store_true",
                   help="Override the dup-summary guard. Use for intentional "
                        "re-emission of an unchanged state snapshot.")
    a.set_defaults(func=cmd_append)

    s = sub.add_parser("show", help="Print an entry by id")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    l = sub.add_parser("list", help="List entries in a file")
    l.add_argument("--file", required=True)
    l.add_argument("--kind")
    l.add_argument("--correlation")
    l.set_defaults(func=cmd_list)

    la = sub.add_parser("latest", help="Print the N most recent entries")
    la.add_argument("--file", required=True)
    la.add_argument("--kind", required=True)
    la.add_argument("--n", type=int, default=1)
    la.set_defaults(func=cmd_latest)

    v = sub.add_parser("validate", help="Validate memory files")
    v.add_argument("--path", type=Path)
    v.add_argument("--strict", action="store_true")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("current-id", help="Print the active correlation_id")
    c.set_defaults(func=cmd_current_id)

    b = sub.add_parser("bump-task", help="Increment task_seq; returns new correlation_id")
    b.set_defaults(func=cmd_bump_task)

    se = sub.add_parser("search", help="Search entries by text / tag / kind / status / date")
    se.add_argument("--query", default="", help="Text substring to match in summary or body")
    se.add_argument("--file", default="", help="Restrict to one file (default: all)")
    se.add_argument("--tag", default="", help="Require a tag")
    se.add_argument("--kind", default="", help="Filter by kind")
    se.add_argument("--status", default="", help="Filter by status")
    se.add_argument("--author", default="", help="Filter by author (substring)")
    se.add_argument("--since", default="", help="YYYY-MM-DD lower bound on `at`")
    se.add_argument("--until", default="", help="YYYY-MM-DD upper bound on `at`")
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=cmd_search)

    r = sub.add_parser("rotate", help="Archive older entries to a dated sidecar file")
    r.add_argument("--file", required=True, help="session-handoff | decisions | patterns (or filename)")
    r.add_argument("--keep-last", type=int, default=None,
                   help="Keep the N most recent entries in the live file")
    r.add_argument("--since", default="",
                   help="Alternative: keep entries whose `at` >= this date (YYYY-MM-DD)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--force", action="store_true",
                   help="Rotate even if the file has validation errors (not recommended)")
    r.set_defaults(func=cmd_rotate)

    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
