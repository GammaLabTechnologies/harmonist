#!/usr/bin/env python3
"""
project_context.py -- extract authoritative project sections from the
NEAREST `AGENTS.md` and emit a compact preamble the orchestrator
prepends to every subagent invocation.

Why: persona agents have their own opinions ("always use X framework")
that may conflict with the project's invariants. The preamble injects
those invariants into the subagent's prompt so the persona sees the
authoritative rules before it acts.

Monorepo support: pass `--focus <path>` with the file or directory
the task touches; the script walks up from that file looking for the
first AGENTS.md, then (optionally) appends the root project's
AGENTS.md underneath as a secondary layer. This lets per-package
AGENTS.md override root-level defaults. Without --focus the script
walks up from CWD as before.

Sections extracted (best-effort; missing sections are silently skipped):
  * Platform Stack
  * Modules
  * Invariants
  * Resilience (only when it fits in the budget)

Usage:
    project_context.py [--path <AGENTS.md>] [--max-chars N]
    project_context.py --focus src/services/payments/handler.py
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
import re
import sys
from pathlib import Path

DEFAULT_SECTIONS = ["Platform Stack", "Modules", "Invariants"]
MAX_CHARS_DEFAULT = 1800  # keep the preamble small; reviewers can widen via CLI


def find_agents_md(start: Path) -> Path | None:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for _ in range(12):
        candidate = cur / "AGENTS.md"
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def find_all_agents_md_upwards(start: Path, stop_at: Path | None = None) -> list[Path]:
    """Return every AGENTS.md from `start` up to filesystem root (or
    `stop_at` inclusive). Order: NEAREST first, ROOT last. Enables the
    monorepo case where a per-package AGENTS.md adds local overrides
    on top of the root project's AGENTS.md."""
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    found: list[Path] = []
    stop_resolved = stop_at.resolve() if stop_at else None
    for _ in range(12):
        candidate = cur / "AGENTS.md"
        if candidate.exists() and candidate not in found:
            found.append(candidate)
        if stop_resolved is not None and cur == stop_resolved:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return found


def extract_sections(text: str, wanted: list[str]) -> dict[str, str]:
    """Return {heading: section-body} for each `## heading` we can find.

    Matching is case-insensitive and ignores trailing descriptors so
    `## Invariants` and `## Invariants (non-negotiable)` both match.
    """
    # Anchor on level-2 headings.
    sections: dict[str, str] = {}
    pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        # Match any wanted heading prefix (case-insensitive).
        for want in wanted:
            if title.lower().startswith(want.lower()):
                start = m.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                sections[want] = text[start:end].strip()
                break
    return sections


def format_preamble(sections: dict[str, str], max_chars: int,
                    order: list[str] | None = None) -> str:
    """Flatten extracted sections into a bounded preamble string.

    `order` is the list of requested section titles (e.g. from --section);
    sections are emitted in that order. Defaults to DEFAULT_SECTIONS.
    """
    lines: list[str] = [
        "PROJECT PRECEDENCE (authoritative — overrides any persona advice):",
        "",
    ]
    budget = max(0, max_chars - len("\n".join(lines)) - 64)

    for title in (order or DEFAULT_SECTIONS):
        if title not in sections:
            continue
        body = sections[title].strip()
        # Keep section bounded. Cut at a reasonable line boundary.
        if len(body) > budget // 2:
            # Crude trim: keep the first few bullet points / sentences.
            cut = body[: budget // 2].rsplit("\n", 1)[0]
            body = cut.rstrip() + "\n… (truncated; see full AGENTS.md)"
        lines.append(f"## {title}")
        lines.append(body)
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > max_chars:
        text = text[: max_chars - 32].rstrip() + "\n… (truncated)\n"
    return text


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path,
                    help="Path to the project's AGENTS.md. Default: walk up from CWD.")
    ap.add_argument("--focus", type=Path,
                    help="File or directory the task touches (monorepo). Walks up from "
                         "this path to find the nearest AGENTS.md; root AGENTS.md is "
                         "appended underneath for layered overrides.")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS_DEFAULT,
                    help="Soft cap on preamble length. Sections exceeding it are truncated.")
    ap.add_argument("--section", action="append",
                    help="Override list of sections to extract (repeatable). Default: Platform Stack, Modules, Invariants.")
    args = ap.parse_args(argv)

    wanted = args.section or DEFAULT_SECTIONS
    paths: list[Path] = []

    if args.path:
        paths = [args.path]
    elif args.focus:
        paths = find_all_agents_md_upwards(args.focus)
    else:
        p = find_agents_md(Path.cwd())
        if p:
            paths = [p]

    paths = [p for p in paths if p.exists()]
    if not paths:
        print("project_context: no AGENTS.md found.", file=sys.stderr)
        return 1

    # Merge: NEAREST first, root last. Root sections that the nearer
    # AGENTS.md already defines are dropped (package overrides root).
    merged: dict[str, str] = {}
    layer_sources: list[str] = []
    for p in paths:
        # Read-only extraction: errors="replace" so one bad byte in a
        # user's AGENTS.md can't kill the whole preamble injection.
        text = p.read_text(encoding="utf-8", errors="replace")
        sections = extract_sections(text, wanted)
        if not sections:
            continue
        layer_sources.append(str(p))
        for k, v in sections.items():
            merged.setdefault(k, v)
    if not merged:
        print("project_context: AGENTS.md found but no recognizable sections.",
              file=sys.stderr)
        return 1

    out = format_preamble(merged, args.max_chars, wanted)
    if len(paths) > 1:
        # Monorepo footer: document which AGENTS.md contributed so a
        # human reading the preamble can trace decisions.
        footer = ("# Preamble sources (nearest -> root):\n# - "
                  + "\n# - ".join(layer_sources)
                  + "\n")
        out = out.rstrip() + "\n\n" + footer
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
