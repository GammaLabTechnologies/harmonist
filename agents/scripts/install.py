#!/usr/bin/env python3
"""
install.py -- Install agents into your local agentic tool(s).

Cross-platform (macOS / Linux / Windows) Python port of install.sh. Reads
converted files from integrations/ (run convert.py first) and copies them
to the appropriate config directory for each tool.

Usage:
    python3 agents/scripts/install.py [--tool <name>] [--interactive]
            [--no-interactive] [--parallel] [--jobs N]

Tools:
    claude-code  -- Copy agents to ~/.claude/agents/
    copilot      -- Copy agents to ~/.github/agents/ and ~/.copilot/agents/
    antigravity  -- Copy skills to ~/.gemini/antigravity/skills/
    gemini-cli   -- Install extension to ~/.gemini/extensions/harmonist/
    opencode     -- Copy agents to .opencode/agents/ in current directory
    cursor       -- Copy rules to .cursor/rules/ in current directory
    aider        -- Copy CONVENTIONS.md to current directory
    windsurf     -- Copy .windsurfrules to current directory
    openclaw     -- Copy workspaces to ~/.openclaw/harmonist/
    qwen         -- Copy SubAgents to .qwen/agents/ in current directory
    kimi         -- Copy agents to ~/.config/kimi/agents/
    all          -- Install for all detected tools (default)

Flags:
    --tool <name>     Install only the specified tool
    --interactive     Show interactive selector (default when run in a TTY)
    --no-interactive  Skip selector, install all detected tools
    --parallel        Run install for each selected tool concurrently
    --jobs N          Max parallel workers (default: cpu count)

Platform support: Linux, macOS, Windows (native -- no bash required).
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
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Mirrors install.sh: "REPO_ROOT" is the agents/ dir (script dir's parent);
# integrations/ and the agent category dirs live directly beneath it.
HERE = Path(__file__).resolve().parent
AGENTS_ROOT = HERE.parent
INTEGRATIONS = AGENTS_ROOT / "integrations"
HOME = Path.home()

ALL_TOOLS = ["claude-code", "copilot", "antigravity", "gemini-cli", "opencode",
             "openclaw", "cursor", "aider", "windsurf", "qwen", "kimi"]

# Agent category dirs, discovered at runtime the same way lint_agents.py
# does (agents/* minus the non-agent helper dirs) -- no sync list to forget.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from migrate_schema import NON_AGENT_DIRS  # noqa: E402

AGENT_DIRS = sorted(
    d.name for d in AGENTS_ROOT.iterdir()
    if d.is_dir() and d.name not in NON_AGENT_DIRS and not d.name.startswith(".")
)


# --- Colour helpers ---------------------------------------------------------

def _supports_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR") \
        and os.environ.get("TERM") != "dumb"


if _supports_color():
    C_GREEN, C_YELLOW, C_RED, C_CYAN, C_BOLD, C_DIM, C_RESET = (
        "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0;36m",
        "\033[1m", "\033[2m", "\033[0m")
else:
    C_GREEN = C_YELLOW = C_RED = C_CYAN = C_BOLD = C_DIM = C_RESET = ""


def ok(msg: str) -> None:
    print(f"{C_GREEN}[OK]{C_RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"{C_YELLOW}[!!]{C_RESET}  {msg}")


def err(msg: str) -> None:
    print(f"{C_RED}[ERR]{C_RESET} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"\n{C_BOLD}{msg}{C_RESET}")


def dim(msg: str) -> None:
    print(f"{C_DIM}{msg}{C_RESET}")


# --- Helpers ----------------------------------------------------------------

def _on_path(exe: str) -> bool:
    return shutil.which(exe) is not None


def _iter_agent_md() -> "list[Path]":
    """Every agent *.md (recursive) under the category dirs whose first line
    is a frontmatter delimiter."""
    out: list[Path] = []
    for d in AGENT_DIRS:
        dirpath = AGENTS_ROOT / d
        if not dirpath.is_dir():
            continue
        for f in sorted(dirpath.rglob("*.md")):
            try:
                with f.open("r", encoding="utf-8") as fh:
                    if fh.readline().rstrip("\n") == "---":
                        out.append(f)
            except Exception:
                continue
    return out


# --- Tool detection ---------------------------------------------------------

def is_detected(tool: str) -> bool:
    if tool == "claude-code":
        return (HOME / ".claude").is_dir()
    if tool == "copilot":
        return _on_path("code") or (HOME / ".github").is_dir() or (HOME / ".copilot").is_dir()
    if tool == "antigravity":
        return (HOME / ".gemini" / "antigravity" / "skills").is_dir()
    if tool == "gemini-cli":
        return _on_path("gemini") or (HOME / ".gemini").is_dir()
    if tool == "cursor":
        return _on_path("cursor") or (HOME / ".cursor").is_dir()
    if tool == "opencode":
        return _on_path("opencode") or (HOME / ".config" / "opencode").is_dir()
    if tool == "aider":
        return _on_path("aider")
    if tool == "openclaw":
        return _on_path("openclaw") or (HOME / ".openclaw").is_dir()
    if tool == "windsurf":
        return _on_path("windsurf") or (HOME / ".codeium").is_dir()
    if tool == "qwen":
        return _on_path("qwen") or (HOME / ".qwen").is_dir()
    if tool == "kimi":
        return _on_path("kimi")
    return False


def tool_label(tool: str) -> str:
    labels = {
        "claude-code": ("Claude Code", "(claude.ai/code)"),
        "copilot":     ("Copilot", "(~/.github + ~/.copilot)"),
        "antigravity": ("Antigravity", "(~/.gemini/antigravity)"),
        "gemini-cli":  ("Gemini CLI", "(gemini extension)"),
        "opencode":    ("OpenCode", "(opencode.ai)"),
        "openclaw":    ("OpenClaw", "(~/.openclaw/harmonist)"),
        "cursor":      ("Cursor", "(.cursor/rules)"),
        "aider":       ("Aider", "(CONVENTIONS.md)"),
        "windsurf":    ("Windsurf", "(.windsurfrules)"),
        "qwen":        ("Qwen Code", "(.qwen/agents)"),
        "kimi":        ("Kimi Code", "(~/.config/kimi/agents)"),
    }
    name, detail = labels.get(tool, (tool, ""))
    return f"{name:<14}  {detail}"


# --- Installers -------------------------------------------------------------

def install_claude_code() -> None:
    dest = HOME / ".claude" / "agents"
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in _iter_agent_md():
        shutil.copy2(f, dest / f.name)
        count += 1
    ok(f"Claude Code: {count} agents -> {dest}")


def install_copilot() -> None:
    dest_github = HOME / ".github" / "agents"
    dest_copilot = HOME / ".copilot" / "agents"
    dest_github.mkdir(parents=True, exist_ok=True)
    dest_copilot.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in _iter_agent_md():
        shutil.copy2(f, dest_github / f.name)
        shutil.copy2(f, dest_copilot / f.name)
        count += 1
    ok(f"Copilot: {count} agents -> {dest_github}")
    ok(f"Copilot: {count} agents -> {dest_copilot}")


def install_antigravity() -> None:
    src = INTEGRATIONS / "antigravity"
    dest = HOME / ".gemini" / "antigravity" / "skills"
    if not src.is_dir():
        err("integrations/antigravity missing. Run convert.py first.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        skill = d / "SKILL.md"
        if not skill.exists():
            continue
        (dest / d.name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill, dest / d.name / "SKILL.md")
        count += 1
    ok(f"Antigravity: {count} skills -> {dest}")


def install_gemini_cli() -> None:
    src = INTEGRATIONS / "gemini-cli"
    dest = HOME / ".gemini" / "extensions" / "harmonist"
    manifest = src / "gemini-extension.json"
    skills_dir = src / "skills"
    if not src.is_dir() or not manifest.exists() or not skills_dir.is_dir():
        err("integrations/gemini-cli incomplete. Run convert.py --tool gemini-cli first.")
        return
    (dest / "skills").mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, dest / "gemini-extension.json")
    count = 0
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill = d / "SKILL.md"
        if not skill.exists():
            continue
        (dest / "skills" / d.name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill, dest / "skills" / d.name / "SKILL.md")
        count += 1
    ok(f"Gemini CLI: {count} skills -> {dest}")


def install_opencode() -> None:
    src = INTEGRATIONS / "opencode"
    dest = Path.cwd() / ".opencode" / "agents"
    if not src.is_dir():
        err("integrations/opencode missing. Run convert.py first.")
        return
    search_dir = src / "agents" if (src / "agents").is_dir() else src
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(search_dir.glob("*.md")):
        if f.name == "README.md":
            continue
        shutil.copy2(f, dest / f.name)
        count += 1
    if count == 0:
        warn(f"OpenCode: no agent files found in {search_dir}. Run convert.py --tool opencode first.")
    else:
        ok(f"OpenCode: {count} agents -> {dest}")
    warn("OpenCode: project-scoped. Run from your project root to install there.")


def install_openclaw() -> None:
    src = INTEGRATIONS / "openclaw"
    dest = HOME / ".openclaw" / "harmonist"
    if not src.is_dir():
        err("integrations/openclaw missing. Run convert.py first.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    have_cli = _on_path("openclaw")
    existing = ""
    if have_cli:
        try:
            import subprocess
            r = subprocess.run(["openclaw", "agents", "list", "--json"],
                               capture_output=True, text=True)
            existing = r.stdout or ""
        except Exception:
            existing = ""
    count = 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        needed = [d / "SOUL.md", d / "AGENTS.md", d / "IDENTITY.md"]
        if not all(p.exists() for p in needed):
            continue
        (dest / d.name).mkdir(parents=True, exist_ok=True)
        for p in needed:
            shutil.copy2(p, dest / d.name / p.name)
        if have_cli and f'"{d.name}"' not in existing:
            try:
                import subprocess
                subprocess.run(["openclaw", "agents", "add", d.name,
                                "--workspace", str(dest / d.name), "--non-interactive"],
                               check=False)
            except Exception:
                pass
        count += 1
    if count == 0:
        err("integrations/openclaw contains no generated workspaces. Run convert.py --tool openclaw first.")
        return
    ok(f"OpenClaw: {count} workspaces -> {dest}")
    if have_cli:
        warn("OpenClaw: run 'openclaw gateway restart' to activate new agents")


def install_cursor() -> None:
    src = INTEGRATIONS / "cursor" / "rules"
    dest = Path.cwd() / ".cursor" / "rules"
    if not src.is_dir():
        err("integrations/cursor missing. Run convert.py first.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(src.glob("*.mdc")):
        shutil.copy2(f, dest / f.name)
        count += 1
    ok(f"Cursor: {count} rules -> {dest}")
    warn("Cursor: project-scoped. Run from your project root to install there.")


def install_aider() -> None:
    src = INTEGRATIONS / "aider" / "CONVENTIONS.md"
    dest = Path.cwd() / "CONVENTIONS.md"
    if not src.exists():
        err("integrations/aider/CONVENTIONS.md missing. Run convert.py first.")
        return
    if dest.exists():
        warn(f"Aider: CONVENTIONS.md already exists at {dest} (remove to reinstall).")
        return
    shutil.copy2(src, dest)
    ok(f"Aider: installed -> {dest}")
    warn("Aider: project-scoped. Run from your project root to install there.")


def install_windsurf() -> None:
    src = INTEGRATIONS / "windsurf" / ".windsurfrules"
    dest = Path.cwd() / ".windsurfrules"
    if not src.exists():
        err("integrations/windsurf/.windsurfrules missing. Run convert.py first.")
        return
    if dest.exists():
        warn(f"Windsurf: .windsurfrules already exists at {dest} (remove to reinstall).")
        return
    shutil.copy2(src, dest)
    ok(f"Windsurf: installed -> {dest}")
    warn("Windsurf: project-scoped. Run from your project root to install there.")


def install_qwen() -> None:
    src = INTEGRATIONS / "qwen" / "agents"
    dest = Path.cwd() / ".qwen" / "agents"
    if not src.is_dir():
        err("integrations/qwen missing. Run convert.py first.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(src.glob("*.md")):
        shutil.copy2(f, dest / f.name)
        count += 1
    ok(f"Qwen Code: installed {count} agents to {dest}")
    warn("Qwen Code: project-scoped. Run from your project root to install there.")
    warn("Tip: Run '/agents manage' in Qwen Code to refresh, or restart session")


def install_kimi() -> None:
    src = INTEGRATIONS / "kimi"
    dest = HOME / ".config" / "kimi" / "agents"
    if not src.is_dir():
        err("integrations/kimi missing. Run convert.py first.")
        return
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        agent_yaml = d / "agent.yaml"
        system_md = d / "system.md"
        if not (agent_yaml.exists() and system_md.exists()):
            continue
        (dest / d.name).mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_yaml, dest / d.name / "agent.yaml")
        shutil.copy2(system_md, dest / d.name / "system.md")
        count += 1
    ok(f"Kimi Code: installed {count} agents to {dest}")
    ok("Usage: kimi --agent-file ~/.config/kimi/agents/<agent-name>/agent.yaml")


_INSTALLERS = {
    "claude-code": install_claude_code,
    "copilot": install_copilot,
    "antigravity": install_antigravity,
    "gemini-cli": install_gemini_cli,
    "opencode": install_opencode,
    "openclaw": install_openclaw,
    "cursor": install_cursor,
    "aider": install_aider,
    "windsurf": install_windsurf,
    "qwen": install_qwen,
    "kimi": install_kimi,
}


def install_tool(tool: str) -> None:
    fn = _INSTALLERS.get(tool)
    if fn:
        fn()


# --- Interactive selector (cross-platform, no ANSI cursor games) ------------

def interactive_select() -> "list[str]":
    detected = {t: is_detected(t) for t in ALL_TOOLS}
    selected = {t for t, d in detected.items() if d}

    while True:
        header("Agent Catalog -- Tool Installer")
        dim("  System scan:  [*] = detected on this machine")
        print("")
        for i, t in enumerate(ALL_TOOLS, 1):
            dot = f"{C_GREEN}[*]{C_RESET}" if detected[t] else f"{C_DIM}[ ]{C_RESET}"
            chk = f"{C_GREEN}[x]{C_RESET}" if t in selected else f"{C_DIM}[ ]{C_RESET}"
            print(f"  {chk}  {i:>2})  {dot}  {tool_label(t)}")
        print("")
        print(f"  {C_CYAN}[1-{len(ALL_TOOLS)}]{C_RESET} toggle   "
              f"{C_CYAN}[a]{C_RESET} all   {C_CYAN}[n]{C_RESET} none   "
              f"{C_CYAN}[d]{C_RESET} detected")
        print(f"  {C_GREEN}[Enter]{C_RESET} install   {C_RED}[q]{C_RESET} quit")
        try:
            raw = input("  >> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return []

        low = raw.lower()
        if low in ("q", "quit"):
            ok("Aborted.")
            return []
        if low == "a":
            selected = set(ALL_TOOLS)
            continue
        if low == "n":
            selected = set()
            continue
        if low == "d":
            selected = {t for t, d in detected.items() if d}
            continue
        if raw == "":
            if selected:
                break
            warn("Nothing selected -- pick a tool or press q to quit.")
            continue
        toggled = False
        for tok in raw.replace(",", " ").split():
            if tok.isdigit():
                idx = int(tok) - 1
                if 0 <= idx < len(ALL_TOOLS):
                    t = ALL_TOOLS[idx]
                    selected.discard(t) if t in selected else selected.add(t)
                    toggled = True
        if not toggled:
            warn(f"Invalid. Enter a number 1-{len(ALL_TOOLS)}, or a command.")

    return [t for t in ALL_TOOLS if t in selected]


# --- Entry point ------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Install converted agents into local AI tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", default="all",
                    help="Install only this tool (default: all).")
    ap.add_argument("--interactive", dest="interactive", action="store_true",
                    default=None, help="Force the interactive selector.")
    ap.add_argument("--no-interactive", dest="interactive", action="store_false",
                    help="Skip the selector; install all detected tools.")
    ap.add_argument("--parallel", action="store_true",
                    help="Install selected tools concurrently.")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="Max parallel workers (default: cpu count).")
    args = ap.parse_args(argv)

    if not INTEGRATIONS.is_dir():
        err("integrations/ not found. Run convert.py first.")
        return 1

    if args.tool != "all" and args.tool not in ALL_TOOLS:
        err(f"Unknown tool '{args.tool}'. Valid: {' '.join(ALL_TOOLS)}")
        return 1

    # Decide whether to show the interactive UI.
    if args.interactive is True:
        use_interactive = True
    elif args.interactive is False:
        use_interactive = False
    else:  # auto
        use_interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                           and args.tool == "all")

    if use_interactive:
        selected = interactive_select()
    elif args.tool != "all":
        selected = [args.tool]
    else:
        header("Agent Catalog -- Scanning for installed tools...")
        print("")
        selected = []
        for t in ALL_TOOLS:
            if is_detected(t):
                selected.append(t)
                print(f"  {C_GREEN}[*]{C_RESET}  {tool_label(t)}  {C_DIM}detected{C_RESET}")
            else:
                print(f"  {C_DIM}[ ]  {tool_label(t)}  not found{C_RESET}")

    if not selected:
        warn("No tools selected or detected. Nothing to install.")
        print("")
        dim(f"  Tip: use --tool <name> to force-install a specific tool.")
        dim(f"  Available: {' '.join(ALL_TOOLS)}")
        return 0

    header("Agent Catalog -- Installing agents")
    print(f"  Repo:       {AGENTS_ROOT}")
    print(f"  Installing: {' '.join(selected)}")
    print("")

    if args.parallel and len(selected) > 1:
        ok(f"Installing {len(selected)} tools in parallel.")
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            list(pool.map(install_tool, selected))
    else:
        n = len(selected)
        for i, t in enumerate(selected, 1):
            print(f"  {C_DIM}[{i}/{n}]{C_RESET} {t}")
            install_tool(t)

    header(f"Done!  Installed {len(selected)} tool(s).")
    print("")
    dim("  Run convert.py to regenerate after adding or editing agents.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
