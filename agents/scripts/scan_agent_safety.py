#!/usr/bin/env python3
"""
scan_agent_safety.py -- scan agent markdown files for prompt-injection
and exfiltration patterns. Agents are copy-pasted into
`.cursor/agents/` and become part of the orchestrator's prompt
context, so a hostile body can change the orchestrator's behaviour
silently.

This scanner is heuristic, not exhaustive: a determined attacker can
always obfuscate. The goal is to raise the cost of a drive-by prompt
injection (e.g. a contributor PR that adds "override safety rules" to
a specialist's body) to the level where it's visible in review.

Categories of patterns:

  1. **Override attempts** -- "ignore previous instructions", "forget
     what the system prompt said", "disable safety", "override rules",
     etc.
  2. **Exfiltration** -- instructions to send, POST, leak, dump
     secrets, `.env`, API keys, system prompts, or the contents of
     `.cursor/memory/` or `~/.ssh/`.
  3. **Remote execution** -- `curl ... | bash`, `wget ... | sh`,
     base64-decoded payload execution, hard-coded callback hosts
     (pastebin, ngrok, webhook.site, transfer.sh, ...).
  4. **Policy subversion** -- "always approve", "skip qa-verifier",
     "don't run security-reviewer", "mark review complete without
     checking".

Usage:
    # Default: scan every agent in the pack (agents/*/*.md).
    python3 agents/scripts/scan_agent_safety.py

    # Scan a project's installed set after integration.
    python3 agents/scripts/scan_agent_safety.py --project /path/to/project

    # Scan an arbitrary file or folder.
    python3 agents/scripts/scan_agent_safety.py --path /tmp/some-agent.md

    # CI-friendly machine output.
    python3 agents/scripts/scan_agent_safety.py --json

Exit codes:
    0 -- no findings above the `warn` severity
    1 -- at least one `error`-severity finding (HARD FAIL)
    2 -- scanner crash / bad arguments
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
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK_ROOT = HERE.parent.parent


# ---------------------------------------------------------------------------
# Pattern catalogue. Each rule is (id, severity, regex, explanation).
# severity:
#   error   -- hard fail; scanner exits 1
#   warn    -- suspicious but non-fatal; prints but exits 0
#   info    -- low signal; only prints with --verbose
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    severity: str
    regex: re.Pattern
    explanation: str


def _c(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


RULES: list[Rule] = [
    # --- Override attempts ---------------------------------------------------
    Rule("override.ignore-instructions", "error",
         _c(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)"),
         "Classic prompt-injection opener."),
    Rule("override.forget-system", "error",
         _c(r"forget\s+(everything|what|the)\s+.{0,30}(system\s*prompt|instructions?)"),
         "Instruction to discard the system prompt."),
    Rule("override.disable-safety", "error",
         # Verb-form instruction ("please disable safety", "you must bypass guardrails").
         # Excludes threat-modelling docs that describe the technique ("T1562.001
         # Disable Security Tools", "attackers bypass guardrails") by requiring a
         # second-person subject or an infinitive-to-do-it framing.
         _c(r"(please|you\s+(must|should|will|need\s+to)|i\s+(want|need)\s+you\s+to|let'?s?)\s+"
            r"(disable|bypass|turn\s*off|skip)\s+.{0,30}"
            r"(safety|guardrails?|content\s*polic(y|ies)|moderation|(the\s+)?(ai\s+)?rules)"),
         "Imperative instruction to disable safety rails."),
    Rule("override.jailbreak-markers", "error",
         _c(r"(DAN\s+mode|jailbreak|developer\s+mode\s+enabled|act\s+as\s+.{0,30}no\s+restrictions?)"),
         "Known jailbreak vocabulary."),
    Rule("override.pretend-new-role", "warn",
         _c(r"you\s+are\s+now\s+(a\s+)?(different|new|replacement)\s+"),
         "Identity override ('you are now a different AI')."),

    # --- Exfiltration --------------------------------------------------------
    Rule("exfil.env-file", "error",
         _c(r"(read|leak|exfiltrate|send|post|upload|print|cat|dump|show)\s+[^\n]{0,80}\.env\b"),
         "Instructions to read or exfiltrate .env files."),
    Rule("exfil.api-keys", "error",
         # Tight: exfiltration-specific verbs + secret noun, NOT doc patterns like
         # "API Keys", "refresh tokens", "retrieve credentials from vault".
         _c(r"(leak|exfiltrate|send|post|upload|ex-filtrate|steal|harvest)"
            r"\s+(the|your|all|any|my|user'?s?|every)?\s*"
            r"(api\s*keys?|secrets?|credentials?|tokens?|\.env|env\s+vars?|"
            r"environment\s+variables?)\s+(to|via|through|into)\s+"),
         "Instructions to exfiltrate API keys / secrets / tokens to a destination."),
    Rule("exfil.system-prompt", "error",
         _c(r"(reveal|leak|dump|print|send|post|show|repeat)\s+[^\n]{0,60}(system\s*prompt|your\s+(initial\s+)?instructions?|your\s+configuration)"),
         "Instructions to reveal the system prompt."),
    Rule("exfil.ssh-keys", "error",
         _c(r"(read|cat|send|upload|print)\s+[^\n]{0,80}(~/\.ssh|id_rsa|id_ed25519|\.ssh/[^\s]+)"),
         "SSH private-key access instruction."),
    Rule("exfil.memory-dir", "error",
         # Only flag when the instruction pairs memory access with an
         # exfil-shaped verb or destination. Reading memory as part of
         # the protocol (which the orchestrator does by design) is not
         # exfiltration.
         _c(r"(send|upload|post|leak|exfiltrate|dump|ship)\s+[^\n]{0,80}\.cursor/memory|"
            r"\.cursor/memory[^\n]{0,80}\s+(to|via)\s+https?://"),
         "Orchestrator memory exfiltration."),
    Rule("exfil.hooks-state", "error",
         _c(r"(read|cat|send|upload|edit)\s+[^\n]{0,80}\.cursor/hooks/\.state"),
         "Attempt to read / modify enforcement state."),

    # --- Remote execution ----------------------------------------------------
    Rule("rex.curl-pipe-sh", "error",
         _c(r"(curl|wget)\s+(-[a-zA-Z]+\s+)*\S*https?://\S+[^\n]{0,30}\|\s*(ba)?sh\b"),
         "Pipe-to-shell remote execution pattern."),
    Rule("rex.base64-decode-exec", "error",
         _c(r"base64\s+(--?d(ecode)?|-d)\s*(\||<<<|\()\s*.*\|\s*(ba)?sh\b"),
         "Decode-and-execute payload pattern."),
    Rule("rex.callback-host", "error",
         _c(r"https?://(?:[^\s/]+\.)?(pastebin\.com|ngrok\.io|ngrok-free\.app|webhook\.site|requestbin|transfer\.sh|bashupload\.com|oast\.pro|burpcollaborator\.net|interact\.sh)\b"),
         "Known data-exfiltration / callback host."),
    Rule("rex.reverse-shell", "error",
         _c(r"(bash|sh|nc|ncat|python)\s+.{0,60}(/dev/tcp/|\-e\s+/bin/(ba)?sh|reverse[-_ ]?shell)"),
         "Reverse-shell command pattern."),
    Rule("rex.eval-remote", "warn",
         _c(r"eval\s*\(\s*requests?\.get\s*\(|eval\s*\(\s*urlopen\s*\("),
         "Remote-code eval via requests.get / urlopen."),

    # --- Policy subversion ---------------------------------------------------
    Rule("policy.skip-reviewer", "error",
         _c(r"(skip|bypass|don'?t\s+run|never\s+invoke|ignore)\s+[^\n]{0,40}(qa-verifier|security-reviewer|code-quality-auditor)"),
         "Instruction to skip a required reviewer."),
    Rule("policy.always-approve", "error",
         _c(r"(always|automatically|silently)\s+(approve|mark\s+(as\s+)?complete|pass|okay|sign\s*-?off)"),
         "Blanket auto-approval instruction."),
    Rule("policy.suppress-findings", "error",
         _c(r"(don'?t|do\s+not|never)\s+(report|flag|mention|list|surface)\s+[^\n]{0,40}(vuln|issue|finding|bug|secret)"),
         "Instruction to hide findings from the user."),
    Rule("policy.fake-handoff", "warn",
         _c(r"(fabricate|make\s+up|fake|synthesize)\s+[^\n]{0,40}(memory\s*entry|handoff|correlation\s*id)"),
         "Attempt to forge audit trail."),
]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    rule: str
    severity: str
    line: int
    snippet: str
    explanation: str


def _scan_text(text: str, rel: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for rule in RULES:
        for m in rule.regex.finditer(text):
            # Determine line number.
            start = m.start()
            line_no = text.count("\n", 0, start) + 1
            line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
            snippet = line.strip()[:160]
            findings.append(Finding(
                path=rel,
                rule=rule.id,
                severity=rule.severity,
                line=line_no,
                snippet=snippet,
                explanation=rule.explanation,
            ))
    return findings


# Directories whose contents are derived from the source pool (converted
# integration formats). Scanning them duplicates findings from the source
# and doesn't add signal.
_DEFAULT_EXCLUDE_DIR_NAMES = {
    "integrations",
    "templates",
    "scripts",
    "__pycache__",
    ".git",
    ".state",
    "tests",
}


def _iter_agent_files(targets: list[Path], extra_exclude: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    excl = set(_DEFAULT_EXCLUDE_DIR_NAMES) | (extra_exclude or set())
    for t in targets:
        if t.is_file() and t.suffix == ".md":
            files.append(t)
        elif t.is_dir():
            for p in sorted(t.rglob("*.md")):
                if any(seg in excl for seg in p.relative_to(t).parts):
                    continue
                files.append(p)
    return files


def scan(targets: list[Path], base: Path) -> list[Finding]:
    out: list[Finding] = []
    for f in _iter_agent_files(targets):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            rel = f.relative_to(base).as_posix()
        except ValueError:
            rel = f.as_posix()
        out.extend(_scan_text(text, rel))
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render(findings: list[Finding], verbose: bool) -> str:
    if not findings:
        return "  no suspicious patterns found."
    lines: list[str] = []
    by_path: dict[str, list[Finding]] = {}
    for f in findings:
        by_path.setdefault(f.path, []).append(f)
    for path, items in sorted(by_path.items()):
        lines.append(f"  {path}")
        for f in items:
            if not verbose and f.severity == "info":
                continue
            marker = {"error": "✖", "warn": "!", "info": "·"}.get(f.severity, "?")
            lines.append(f"    {marker} {f.rule}  (line {f.line})  -- {f.explanation}")
            lines.append(f"        > {f.snippet}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path,
                    help="Project root; scans .cursor/agents/ under it.")
    ap.add_argument("--path", type=Path, action="append", default=[],
                    help="Additional file or directory to scan. Repeatable.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    # Choose targets.
    targets: list[Path] = []
    base = PACK_ROOT
    if args.project:
        base = args.project.resolve()
        p = base / ".cursor" / "agents"
        if not p.exists():
            print(f"error: {p} does not exist -- nothing to scan", file=sys.stderr)
            return 2
        targets = [p]
    elif args.path:
        targets = [p.resolve() for p in args.path]
        base = Path.cwd().resolve()
    else:
        # Default: pack agents catalog.
        targets = [PACK_ROOT / "agents"]

    try:
        findings = scan(targets, base)
    except Exception as e:
        print(f"scan crashed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "targets": [str(t) for t in targets],
            "findings": [asdict(f) for f in findings],
            "counts": {
                "error": sum(1 for f in findings if f.severity == "error"),
                "warn":  sum(1 for f in findings if f.severity == "warn"),
                "info":  sum(1 for f in findings if f.severity == "info"),
            },
        }, indent=2))
    else:
        print(render(findings, verbose=args.verbose))

    n_errors = sum(1 for f in findings if f.severity == "error")
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
