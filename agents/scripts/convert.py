#!/usr/bin/env python3
"""
convert.py -- Convert pack agent .md files into tool-specific formats.

Cross-platform (macOS / Linux / Windows) Python port of convert.sh. Reads
all agent files from the standard category directories and writes converted
files to integrations/<tool>/. Run this to regenerate all integration files
after adding or modifying agents.

Usage:
    python3 agents/scripts/convert.py [--tool <name>] [--out <dir>]
                                      [--thin] [--parallel] [--jobs N]

Tools:
    antigravity  -- Antigravity skill files (~/.gemini/antigravity/skills/)
    gemini-cli   -- Gemini CLI extension (skills/ + gemini-extension.json)
    opencode     -- OpenCode agent files (.opencode/agents/*.md)
    cursor       -- Cursor rule files (.cursor/rules/*.mdc)
    aider        -- Single CONVENTIONS.md for Aider
    windsurf     -- Single .windsurfrules for Windsurf
    openclaw     -- OpenClaw workspaces (integrations/openclaw/<agent>/SOUL.md)
    qwen         -- Qwen Code SubAgent files (~/.qwen/agents/*.md)
    kimi         -- Kimi Code CLI agent files (~/.config/kimi/agents/)
    all          -- All tools (default)

Output is written to integrations/<tool>/ relative to the repo root. This
script never touches user config dirs -- see install.py for that.

    --thin       Extract the essentials-only variant of each agent before
                 conversion (see scripts/extract_essentials.py). Source files
                 untouched; integration outputs stay under token budget.
    --parallel   Convert independent tools concurrently (output identical;
                 only faster).
    --jobs N     Max parallel workers when using --parallel (default: cpu count).
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
# Force UTF-8 on stdio so status glyphs (checkmarks, arrows) print on legacy
# Windows code pages (cp1252) instead of raising UnicodeEncodeError. Reached
# only on Python 3.9+ (older interpreters exit above); a stream without
# .reconfigure (e.g. a captured StringIO) simply keeps its current encoding.
try:
    _asp_sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    _asp_sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
# === PY-GUARD:END ===

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Mirrors convert.sh: its "REPO_ROOT" is the agents/ dir (script dir's
# parent), and agent category dirs + the default integrations/ output live
# directly beneath it.
HERE = Path(__file__).resolve().parent
AGENTS_ROOT = HERE.parent
TODAY = _dt.date.today().strftime("%Y-%m-%d")

# Schema v2 flat layout: every agent lives directly under agents/<category>/.
# Sub-folders (e.g. game-development/unity/) are walked recursively.
# Discovered at runtime the same way lint_agents.py does (agents/* minus
# the non-agent helper dirs), so adding a category never needs a sync edit.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from migrate_schema import NON_AGENT_DIRS  # noqa: E402

AGENT_DIRS = sorted(
    d.name for d in AGENTS_ROOT.iterdir()
    if d.is_dir() and d.name not in NON_AGENT_DIRS and not d.name.startswith(".")
)

VALID_TOOLS = ["antigravity", "gemini-cli", "opencode", "cursor", "aider",
               "windsurf", "openclaw", "qwen", "kimi"]

# Tools whose converters write into per-tool subtrees and can run in parallel.
PARALLEL_TOOLS = ["antigravity", "gemini-cli", "opencode", "cursor",
                  "openclaw", "qwen", "kimi"]


# --- Colour helpers (TTY only) ---------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR") \
        and os.environ.get("TERM") != "dumb"


if _supports_color():
    GREEN, YELLOW, RED, BOLD, RESET = (
        "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[1m", "\033[0m")
else:
    GREEN = YELLOW = RED = BOLD = RESET = ""


def info(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[!!]{RESET}  {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERR]{RESET} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"\n{BOLD}{msg}{RESET}")


# --- Frontmatter helpers ----------------------------------------------------

def _records(text: str) -> list[str]:
    """Mimic awk record splitting: lines on \\n, no trailing empty record."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def get_field(field: str, text: str) -> str:
    """Extract a single field value from the YAML frontmatter block.
    Strips surrounding single or double quotes. Mirrors convert.sh::get_field.
    """
    fm = 0
    prefix = field + ": "
    for line in _records(text):
        if line == "---":
            fm += 1
            continue
        if fm == 1 and line.startswith(prefix):
            raw = line[len(prefix):]
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                raw = raw[1:-1]
            elif len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
                raw = raw[1:-1]
            return raw
    return ""


def get_body(text: str) -> str:
    """Strip the leading frontmatter block, return only the body. Mirrors
    convert.sh: awk emits body lines (fm>=2), then bash `$(...)` command
    substitution strips ALL trailing newlines. Each converter re-adds a
    single trailing newline, so we must strip here to match byte-for-byte."""
    fm = 0
    out: list[str] = []
    for line in _records(text):
        if line == "---":
            fm += 1
            continue
        if fm >= 2:
            out.append(line)
    return "\n".join(out).rstrip("\n")


def slugify(name: str) -> str:
    """'Frontend Developer' -> 'frontend-developer'. Mirrors convert.sh."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# --- Per-tool converters ----------------------------------------------------

def convert_antigravity(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    slug = "pack-" + slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "antigravity" / slug
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "SKILL.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        "risk: low\n"
        "source: community\n"
        f"date_added: '{TODAY}'\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


def convert_gemini_cli(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "gemini-cli" / "skills" / slug
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "SKILL.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: {description}\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


_OPENCODE_COLORS = {
    "cyan": "#00FFFF", "blue": "#3498DB", "green": "#2ECC71", "red": "#E74C3C",
    "purple": "#9B59B6", "orange": "#F39C12", "teal": "#008080",
    "indigo": "#6366F1", "pink": "#E84393", "gold": "#EAB308",
    "amber": "#F59E0B", "neon-green": "#10B981", "neon-cyan": "#06B6D4",
    "metallic-blue": "#3B82F6", "yellow": "#EAB308", "violet": "#8B5CF6",
    "rose": "#F43F5E", "lime": "#84CC16", "gray": "#6B7280",
    "fuchsia": "#D946EF",
}


def resolve_opencode_color(c: str) -> str:
    """Map known color names / normalise to OpenCode-safe #RRGGBB."""
    c = c.strip().lower()
    mapped = _OPENCODE_COLORS.get(c, c)
    if re.match(r"^#[0-9a-fA-F]{6}$", mapped):
        return "#" + mapped[1:].upper()
    if re.match(r"^[0-9a-fA-F]{6}$", mapped):
        return "#" + mapped.upper()
    return "#6B7280"


def convert_opencode(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    color = resolve_opencode_color(get_field("color", file_text))
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "opencode" / "agents"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{slug}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "mode: subagent\n"
        f"color: '{color}'\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


def convert_cursor(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "cursor" / "rules"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{slug}.mdc").write_text(
        "---\n"
        f"description: {description}\n"
        'globs: ""\n'
        "alwaysApply: false\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


# SOUL.md header keywords (case-insensitive) -- everything else -> AGENTS.md.
_SOUL_PATTERNS = [
    re.compile(r"identity"),
    re.compile(r"learning.*memory"),
    re.compile(r"communication"),
    re.compile(r"style"),
    re.compile(r"critical.rule"),
    re.compile(r"rules.you.must.follow"),
]


def convert_openclaw(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "openclaw" / slug
    outdir.mkdir(parents=True, exist_ok=True)

    soul_parts: list[str] = []
    agents_parts: list[str] = []
    current_target = "agents"
    current_section = ""

    def _flush() -> None:
        nonlocal current_section
        if current_section:
            (soul_parts if current_target == "soul" else agents_parts).append(
                current_section)
        current_section = ""

    # Iterate the body line-by-line (body already carries trailing newlines).
    for line in body.split("\n"):
        # Re-add the newline that split() removed, except for the synthetic
        # trailing empty element.
        if re.match(r"^##\s", line):
            _flush()
            header_lower = line.lower()
            current_target = "agents"
            for pat in _SOUL_PATTERNS:
                if pat.search(header_lower):
                    current_target = "soul"
                    break
        current_section += line + "\n"
    _flush()

    soul_content = "".join(soul_parts)
    agents_content = "".join(agents_parts)

    (outdir / "SOUL.md").write_text(f"{soul_content}\n", encoding="utf-8")
    (outdir / "AGENTS.md").write_text(f"{agents_content}\n", encoding="utf-8")

    emoji = get_field("emoji", file_text)
    vibe = get_field("vibe", file_text)
    if emoji and vibe:
        (outdir / "IDENTITY.md").write_text(f"# {emoji} {name}\n{vibe}\n",
                                            encoding="utf-8")
    else:
        (outdir / "IDENTITY.md").write_text(f"# {name}\n{description}\n",
                                            encoding="utf-8")


def convert_qwen(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    tools = get_field("tools", file_text)
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "qwen" / "agents"
    outdir.mkdir(parents=True, exist_ok=True)
    if tools:
        (outdir / f"{slug}.md").write_text(
            "---\n"
            f"name: {slug}\n"
            f"description: {description}\n"
            f"tools: {tools}\n"
            "---\n"
            f"{body}\n",
            encoding="utf-8",
        )
    else:
        (outdir / f"{slug}.md").write_text(
            "---\n"
            f"name: {slug}\n"
            f"description: {description}\n"
            "---\n"
            f"{body}\n",
            encoding="utf-8",
        )


def convert_kimi(file_text: str, out_dir: Path) -> None:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    slug = slugify(name)
    body = get_body(file_text)
    outdir = out_dir / "kimi" / slug
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "agent.yaml").write_text(
        "version: 1\n"
        "agent:\n"
        f"  name: {slug}\n"
        "  extend: default\n"
        "  system_prompt_path: ./system.md\n",
        encoding="utf-8",
    )
    (outdir / "system.md").write_text(
        f"# {name}\n\n{description}\n\n{body}\n",
        encoding="utf-8",
    )


def _aider_header() -> str:
    return (
        "# AI Agent Conventions\n"
        "#\n"
        "# This file provides Aider with the full roster of specialized AI agents from\n"
        "# the agent catalog.\n"
        "#\n"
        "# To activate an agent, reference it by name in your Aider session prompt, e.g.:\n"
        '#   "Use the Frontend Developer agent to review this component."\n'
        "#\n"
        "# Generated by scripts/convert.py -- do not edit manually.\n"
        "\n"
    )


def _windsurf_header() -> str:
    return (
        "# AI Agent Rules for Windsurf\n"
        "#\n"
        "# Full roster of specialized AI agents from the agent catalog.\n"
        "# To activate an agent, reference it by name in your Windsurf conversation.\n"
        "#\n"
        "# Generated by scripts/convert.py -- do not edit manually.\n"
        "\n"
    )


def _aider_block(file_text: str) -> str:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    body = get_body(file_text)
    return f"\n---\n\n## {name}\n\n> {description}\n\n{body}\n"


def _windsurf_block(file_text: str) -> str:
    name = get_field("name", file_text)
    description = get_field("description", file_text)
    body = get_body(file_text)
    rule = "=" * 80
    return f"\n{rule}\n## {name}\n{description}\n{rule}\n\n{body}\n\n"


# --- Agent file discovery ---------------------------------------------------

def _iter_agent_files() -> list[Path]:
    """Every *.md under the category dirs (recursive), sorted, that starts
    with a frontmatter delimiter and declares a name."""
    files: list[Path] = []
    for d in AGENT_DIRS:
        dirpath = AGENTS_ROOT / d
        if not dirpath.is_dir():
            continue
        files.extend(sorted(dirpath.rglob("*.md")))
    return files


def _read_effective(path: Path, thin: bool, thin_dir: "Path | None") -> "str | None":
    text = path.read_text(encoding="utf-8")
    first_line = text.split("\n", 1)[0]
    if first_line != "---":
        return None
    if not get_field("name", text):
        # Most often a `name:Foo` (missing space) frontmatter line.
        # Don't drop the file silently -- that hides the agent from every
        # integration output with no trace.
        warn(f"skipping {path.relative_to(AGENTS_ROOT)}: no parseable "
             f"'name:' in frontmatter (expected 'name: <value>')")
        return None
    if thin and thin_dir is not None:
        scratch = thin_dir / path.name
        extractor = HERE / "extract_essentials.py"
        r = subprocess.run([sys.executable, str(extractor), str(path)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            scratch.write_text(r.stdout, encoding="utf-8")
            return scratch.read_text(encoding="utf-8")
    return text


_SINGLE_FILE_TOOLS = {"aider", "windsurf"}
_PER_FILE_CONVERTERS = {
    "antigravity": convert_antigravity,
    "gemini-cli": convert_gemini_cli,
    "opencode": convert_opencode,
    "cursor": convert_cursor,
    "openclaw": convert_openclaw,
    "qwen": convert_qwen,
    "kimi": convert_kimi,
}


def run_conversions(tool: str, out_dir: Path, thin: bool) -> int:
    """Convert every agent for `tool`. Returns the count converted."""
    thin_dir: "Path | None" = None
    if thin:
        thin_dir = Path(tempfile.mkdtemp(prefix="asp-thin-"))
    count = 0
    aider_buf: list[str] = []
    windsurf_buf: list[str] = []
    try:
        for path in _iter_agent_files():
            text = _read_effective(path, thin, thin_dir)
            if text is None:
                continue
            if tool == "aider":
                aider_buf.append(_aider_block(text))
            elif tool == "windsurf":
                windsurf_buf.append(_windsurf_block(text))
            else:
                _PER_FILE_CONVERTERS[tool](text, out_dir)
            count += 1
    finally:
        if thin_dir is not None:
            import shutil
            shutil.rmtree(thin_dir, ignore_errors=True)

    if tool == "aider":
        outdir = out_dir / "aider"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "CONVENTIONS.md").write_text(_aider_header() + "".join(aider_buf),
                                               encoding="utf-8")
        info("Wrote integrations/aider/CONVENTIONS.md")
    elif tool == "windsurf":
        outdir = out_dir / "windsurf"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / ".windsurfrules").write_text(_windsurf_header() + "".join(windsurf_buf),
                                               encoding="utf-8")
        info("Wrote integrations/windsurf/.windsurfrules")
    elif tool == "gemini-cli":
        gdir = out_dir / "gemini-cli"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "gemini-extension.json").write_text(
            '{\n  "name": "harmonist",\n  "version": "1.0.0"\n}\n',
            encoding="utf-8",
        )
        info("Wrote gemini-extension.json")
    return count


# --- Entry point ------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Convert pack agents into tool-specific formats.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", default="all",
                    help="Tool to convert for (default: all). One of: "
                         + ", ".join(VALID_TOOLS) + ", all")
    ap.add_argument("--out", type=Path, default=AGENTS_ROOT / "integrations",
                    help="Output directory (default: agents/integrations).")
    ap.add_argument("--thin", action="store_true",
                    help="Extract essentials-only variant before converting.")
    ap.add_argument("--parallel", action="store_true",
                    help="Convert independent tools concurrently.")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="Max parallel workers (default: cpu count).")
    args = ap.parse_args(argv)

    tool = args.tool
    out_dir: Path = args.out
    if tool != "all" and tool not in VALID_TOOLS:
        error(f"Unknown tool '{tool}'. Valid: {' '.join(VALID_TOOLS + ['all'])}")
        return 1

    header("Agent Catalog -- Converting agents to tool-specific formats")
    print(f"  Repo:   {AGENTS_ROOT}")
    print(f"  Output: {out_dir}")
    print(f"  Tool:   {tool}")
    print(f"  Date:   {TODAY}")

    tools_to_run = VALID_TOOLS if tool == "all" else [tool]

    total = 0
    if args.parallel and tool == "all":
        # Independent per-subtree tools run concurrently; the two single-file
        # accumulators (aider, windsurf) run after, sequentially.
        info(f"Parallel mode: {len(PARALLEL_TOOLS)} tools across {args.jobs} workers.")
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futures = {t: pool.submit(run_conversions, t, out_dir, args.thin)
                       for t in PARALLEL_TOOLS}
            for t, fut in futures.items():
                count = fut.result()
                info(f"Converted {count} agents for {t}")
        for t in ("aider", "windsurf"):
            count = run_conversions(t, out_dir, args.thin)
            total += count
            info(f"Converted {count} agents for {t}")
    else:
        n = len(tools_to_run)
        for i, t in enumerate(tools_to_run, 1):
            header(f"Converting: {t} ({i}/{n})")
            count = run_conversions(t, out_dir, args.thin)
            total += count
            info(f"Converted {count} agents for {t}")

    print("")
    info(f"Done. Total conversions: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
