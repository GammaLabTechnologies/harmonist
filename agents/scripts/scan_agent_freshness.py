#!/usr/bin/env python3
"""
scan_agent_freshness.py -- surface agents that reference deprecated
technologies, stale frameworks, or pre-2023 norms. Also flags agents
that haven't declared `version` / `updated_at` at all.

This is heuristic: the findings are pointers for human review, not
automatic deletions. Tech moves, frameworks get renamed, best
practices shift. The scanner knows about a curated list of common
"this was hot in 2019 but isn't anymore" signals.

Usage:
    python3 agents/scripts/scan_agent_freshness.py
    python3 agents/scripts/scan_agent_freshness.py --project /path  # scans .cursor/agents/
    python3 agents/scripts/scan_agent_freshness.py --json
    python3 agents/scripts/scan_agent_freshness.py --stale-after 365
    python3 agents/scripts/scan_agent_freshness.py --vocab custom-rules.json

The built-in rule list is a 2026 snapshot. Pass `--vocab <file>` to
layer additional rules without forking the script -- useful when
your org deprecates a library the upstream list doesn't know about
yet. Vocab schema in FRESHNESS_VOCAB_SCHEMA below.

Exit codes:
    0  no error findings
    1  at least one error finding
    2  scanner crash / bad input
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
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Deprecated-tech signals. Each = (regex, severity, replacement-hint).
#
# Severity:
#   error   Definitely-dead tech (AngularJS 1.x, Grunt, Bower, Enzyme).
#   warn    Legacy-but-not-quite-dead (moment.js, jest config style,
#           older React patterns, Webpack 4, Node 14 recommendations).
#   info    Naming changed (e.g. Kotlin Android Extensions -> ViewBinding)
#           or a library has a modern successor.
# ---------------------------------------------------------------------------

FRESHNESS_VOCAB_SCHEMA = """\
External rule files are JSON with either of two shapes:

    // shape 1: plain list of rules
    [
      {
        "id":          "ai.custom-gpt3",
        "severity":    "error",            // error | warn | info
        "pattern":     "(?i)gpt-?3(?!\\\\.5)",
        "message":     "GPT-3 reference",
        "replacement": "gpt-4o or newer"
      }, ...
    ]

    // shape 2: object with `rules` + optional `extend_builtin: false`
    {
      "extend_builtin": true,   // default true; set false to REPLACE
      "rules":          [ ... ]
    }

Patterns are Python regex. `(?i)` prefix enables case-insensitive
matching. Every rule is applied against the body of each agent file
(frontmatter is skipped). The scanner loads --vocab files AFTER the
built-in set; duplicate `id`s override the built-in rule."""


@dataclass
class Signal:
    rule: str
    severity: str
    regex: re.Pattern
    message: str
    replacement: str


def _load_vocab_file(path: Path) -> list[Signal]:
    """Load user-supplied freshness rules. Returns (rules, extend_builtin)
    where extend_builtin True means the rules APPEND to the built-in set,
    False means they REPLACE it."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        raise RuntimeError(f"{path}: invalid JSON ({e})") from e

    if isinstance(data, list):
        rules_raw = data
    elif isinstance(data, dict) and "rules" in data:
        rules_raw = data["rules"]
    else:
        raise RuntimeError(
            f"{path}: expected a list of rules OR an object with `rules` key"
        )

    rules: list[Signal] = []
    for i, r in enumerate(rules_raw):
        try:
            rid = str(r["id"]).strip()
            severity = str(r.get("severity", "warn")).strip().lower()
            if severity not in ("error", "warn", "info"):
                severity = "warn"
            pattern = re.compile(str(r["pattern"]))
            message = str(r.get("message", rid))
            replacement = str(r.get("replacement", ""))
        except KeyError as e:
            raise RuntimeError(f"{path} rule[{i}] missing required field: {e}") from e
        except re.error as e:
            raise RuntimeError(f"{path} rule[{i}] invalid regex: {e}") from e
        rules.append(Signal(rid, severity, pattern, message, replacement))
    return rules


def _extend_builtin_flag(path: Path) -> bool:
    """Read `extend_builtin` flag from a vocab file (defaults to True)."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return True
    if isinstance(data, dict) and "extend_builtin" in data:
        return bool(data["extend_builtin"])
    return True


def _c(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


SIGNALS: list[Signal] = [
    # JavaScript / frontend
    Signal("js.angularjs-1x", "error",
           _c(r"\bangular[.\s]?js\b(?!.*angular\s*\d{2,})|angular\s*1\.[0-9x]"),
           "Reference to AngularJS 1.x", "Angular (2+), React, Vue, Svelte"),
    Signal("js.grunt-bower", "error",
           _c(r"\b(grunt|bower)\b.*(build|package|dependency)"),
           "Grunt or Bower in build tooling",
           "Vite, esbuild, turbo, rollup"),
    Signal("js.enzyme", "error",
           _c(r"\benzyme\b.*(mount|shallow|render)"),
           "Enzyme for React tests", "@testing-library/react"),
    Signal("js.moment", "warn",
           _c(r"\bmoment(\.js)?\b.*(parse|format|add|subtract)"),
           "moment.js — in maintenance mode since 2020",
           "date-fns, Luxon, Temporal, Day.js"),
    Signal("js.webpack4-or-lower", "warn",
           _c(r"\bwebpack\s*[1-4](\.[0-9x]+)?\b|webpack@[1-4]"),
           "Webpack 1–4", "Webpack 5+ (or move to Vite/esbuild)"),
    Signal("js.create-react-app", "warn",
           _c(r"\bcreate[-\s]react[-\s]app\b|\bcra\b|react-scripts"),
           "Create-React-App (deprecated 2023)",
           "Vite, Next.js, Remix"),
    Signal("js.class-components", "info",
           _c(r"\bReact\.createClass\b|extends\s+React\.Component\b"),
           "Class components in new React work",
           "Function components + hooks"),

    # Node
    Signal("node.node12-or-lower", "error",
           _c(r"node(js|\s+js)?\s*[vV]?(8|10|12|14)(\.[0-9])?\b|require\([\"']util[\"']\)\.promisify"),
           "Node 14 or lower (EOL)",
           "Node 20 / 22 LTS"),

    # Python
    Signal("py.python2", "error",
           _c(r"\bpython\s*2(\.[0-9])?\b|from\s+__future__\s+import\s+print_function"),
           "Python 2", "Python 3.10+"),
    Signal("py.nose-or-old-unittest", "warn",
           _c(r"\bnose2?\b.*(test|testing)|unittest2"),
           "nose / nose2 test runner (dead)", "pytest"),
    Signal("py.pipenv-only", "warn",
           _c(r"\bpipenv\b(?!.*poetry|.*uv|.*rye)"),
           "pipenv-only workflow",
           "poetry, uv, rye, pdm"),
    Signal("py.distutils", "warn",
           _c(r"from\s+distutils|\bdistutils\.core\b|setup\.py\s+sdist"),
           "distutils (removed in Python 3.12)",
           "setuptools, hatch, uv build, poetry build"),

    # Android
    Signal("android.kae", "warn",
           _c(r"kotlin[-\s]android[-\s]extensions|synthetic\s+imports"),
           "Kotlin Android Extensions (deprecated 2020)",
           "ViewBinding, Jetpack Compose"),
    Signal("android.support-library", "error",
           _c(r"\bandroid\.support\.\w+|com\.android\.support:"),
           "android.support.* library (deprecated since 2018)",
           "AndroidX equivalents"),

    # iOS
    Signal("ios.cocoapods-only", "info",
           _c(r"\bcocoapods\b(?!.*spm|.*swift\s*package)"),
           "CocoaPods-only (Swift Package Manager is the Apple-blessed option)",
           "Swift Package Manager (SPM)"),

    # General
    Signal("general.jquery", "warn",
           _c(r"\bjquery\b(?!.*legacy|.*migration)"),
           "jQuery in new work",
           "native DOM APIs, React/Vue/Svelte, Alpine.js for sprinkles"),
    Signal("general.ie11", "error",
           _c(r"(internet\s+explorer\s*11|IE\s*11|IE11)\s+(support|compat)"),
           "IE11 support (Microsoft ended support 2022)",
           "Drop the polyfills; target evergreen browsers"),
    Signal("general.travis-ci", "warn",
           _c(r"\btravis[-\s]?ci\b|\.travis\.yml"),
           "Travis CI (free tier discontinued 2021)",
           "GitHub Actions, GitLab CI, CircleCI, Buildkite"),
    Signal("general.heroku-free", "info",
           _c(r"heroku\s+(free|hobby)\s+(tier|dyno|plan)"),
           "Heroku free tier (discontinued Nov 2022)",
           "Railway, Render, Fly.io, Heroku paid plans"),
    Signal("general.docker-compose-v1", "warn",
           _c(r"docker[-\s]compose\s*[vV]?1\.|compose\s*file\s*version\s*[\"']?2\b"),
           "docker-compose v1 / compose file v2",
           "docker compose (v2 CLI) + compose.yaml"),
    Signal("general.kubernetes-pre-1-24", "info",
           _c(r"k(ubernetes|8s)\s*[vV]?1\.(1[0-9]|2[0-3])\b"),
           "Kubernetes ≤ 1.23 (EOL; dockershim removed at 1.24)",
           "Kubernetes 1.28+ and containerd"),

    # AI / LLM
    Signal("ai.gpt-3-or-davinci", "error",
           _c(r"text-davinci-003|gpt-3(\.5)?(?!-turbo)|gpt3-only"),
           "text-davinci-003 / GPT-3 (deprecated 2023/2024)",
           "gpt-4o-mini or newer, claude-haiku, gemini-flash"),
    Signal("ai.langchain-v0-early", "info",
           _c(r"langchain@?0\.0\.[0-9]+|langchain\s*<\s*0\.1"),
           "Very old LangChain (pre-0.1; ecosystem rewrote in 2023)",
           "Current LangChain ≥ 0.3, or LlamaIndex, or raw SDK + OTel"),
]


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass
class Finding:
    path: str
    slug: str
    rule: str
    severity: str
    line: int
    snippet: str
    message: str
    replacement: str = ""


@dataclass
class AgentMeta:
    path: str
    slug: str
    version: str
    updated_at: str
    deprecated: bool


def _parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm = m.group(1)
    out: dict = {}
    for line in fm.splitlines():
        ms = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if not ms:
            continue
        out[ms.group(1)] = ms.group(2).strip().strip('"').strip("'")
    return out


def _scan_body(text: str, signals: list[Signal] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    m = FRONTMATTER_RE.match(text)
    body = m.group(2) if m else text
    lines = body.splitlines()
    for sig in (signals if signals is not None else SIGNALS):
        for mm in sig.regex.finditer(body):
            start = mm.start()
            line_no = body.count("\n", 0, start) + 1
            line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
            findings.append(Finding(
                path="",
                slug="",
                rule=sig.rule,
                severity=sig.severity,
                line=line_no,
                snippet=line.strip()[:160],
                message=sig.message,
                replacement=sig.replacement,
            ))
            break  # one hit per signal per file is enough
    return findings


def _iter_agent_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    excl = {"integrations", "templates", "scripts", "__pycache__",
            "tests", ".state", ".git"}
    for t in targets:
        if t.is_file() and t.suffix == ".md":
            out.append(t)
        elif t.is_dir():
            for p in sorted(t.rglob("*.md")):
                if any(seg in excl for seg in p.relative_to(t).parts):
                    continue
                out.append(p)
    return out


def scan(
    targets: list[Path],
    base: Path,
    stale_after_days: int | None = None,
    require_version: bool = False,
    signals: list[Signal] | None = None,
) -> tuple[list[Finding], list[AgentMeta]]:
    findings: list[Finding] = []
    metas: list[AgentMeta] = []
    today = dt.date.today()

    effective_signals = signals if signals is not None else SIGNALS

    for p in _iter_agent_files(targets):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            rel = p.relative_to(base).as_posix()
        except ValueError:
            rel = p.as_posix()
        slug = p.stem
        fm = _parse_frontmatter(text)
        if "category" not in fm:
            # Not an agent (README, CONTRIBUTING, etc.)
            continue

        meta = AgentMeta(
            path=rel,
            slug=slug,
            version=fm.get("version", ""),
            updated_at=fm.get("updated_at", ""),
            deprecated=str(fm.get("deprecated", "")).lower() in ("true", "yes"),
        )
        metas.append(meta)

        # Tech-signal scan on the body.
        for f in _scan_body(text, effective_signals):
            f.path = rel
            f.slug = slug
            findings.append(f)

        # Missing-version metadata check (warn, only if --require-version).
        if require_version and not meta.version:
            findings.append(Finding(
                path=rel, slug=slug, rule="meta.no-version", severity="warn",
                line=0, snippet="",
                message="agent has no `version` in frontmatter",
                replacement='add e.g. `version: "1.0.0"` to the frontmatter',
            ))

        # Stale timestamp check.
        if stale_after_days and meta.updated_at:
            try:
                date_part = meta.updated_at[:10]
                ua = dt.date.fromisoformat(date_part)
                age = (today - ua).days
                if age > stale_after_days:
                    findings.append(Finding(
                        path=rel, slug=slug,
                        rule="meta.stale-updated-at", severity="warn",
                        line=0, snippet=meta.updated_at,
                        message=f"agent not updated in {age} days "
                                f"(threshold {stale_after_days})",
                        replacement="review the body for deprecated tech, "
                                    "update `updated_at`, bump `version`",
                    ))
            except Exception:
                pass

    return (findings, metas)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(findings: list[Finding], metas: list[AgentMeta],
           verbose: bool) -> str:
    out: list[str] = []
    if not findings:
        out.append(f"  no freshness issues found across {len(metas)} agents.")
    else:
        errors = [f for f in findings if f.severity == "error"]
        warns = [f for f in findings if f.severity == "warn"]
        infos = [f for f in findings if f.severity == "info"]
        out.append(
            f"  {len(errors)} error(s), {len(warns)} warning(s), "
            f"{len(infos)} info across {len(metas)} agents:"
        )
        by_path: dict[str, list[Finding]] = {}
        for f in findings:
            by_path.setdefault(f.path, []).append(f)
        for path, items in sorted(by_path.items()):
            out.append(f"\n  {path}")
            for f in items:
                if not verbose and f.severity == "info":
                    continue
                marker = {"error": "✖", "warn": "!", "info": "·"}[f.severity]
                loc = f":{f.line}" if f.line else ""
                out.append(f"    {marker} [{f.rule}]{loc}  -- {f.message}")
                if f.snippet:
                    out.append(f"        > {f.snippet}")
                if f.replacement:
                    out.append(f"        > Consider: {f.replacement}")
    n_versioned = sum(1 for m in metas if m.version)
    n_timestamped = sum(1 for m in metas if m.updated_at)
    n_deprecated = sum(1 for m in metas if m.deprecated)
    out.append("")
    out.append(
        f"  Metadata coverage: {n_versioned}/{len(metas)} carry `version`, "
        f"{n_timestamped}/{len(metas)} carry `updated_at`, "
        f"{n_deprecated} marked `deprecated`."
    )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path,
                    help="Project root; scans .cursor/agents/ under it.")
    ap.add_argument("--path", type=Path, action="append", default=[],
                    help="Additional file/directory to scan. Repeatable.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--require-version", action="store_true",
                    help="Warn on agents without a `version` field.")
    ap.add_argument("--stale-after", type=int, default=None,
                    help="Warn on agents whose `updated_at` is older than "
                         "this many days.")
    ap.add_argument("--vocab", type=Path, action="append", default=[],
                    help="Additional JSON rules file. Repeatable. By default "
                         "rules APPEND to the built-in set; the file may set "
                         "`extend_builtin: false` to REPLACE it. See "
                         "FRESHNESS_VOCAB_SCHEMA in the script for the full "
                         "shape.")
    ap.add_argument("--print-vocab-schema", action="store_true",
                    help="Print the --vocab file schema and exit.")
    args = ap.parse_args(argv)

    if args.print_vocab_schema:
        print(FRESHNESS_VOCAB_SCHEMA)
        return 0

    # Compose the effective rule list from built-ins + user --vocab files.
    effective_signals: list[Signal] = list(SIGNALS)
    extend_builtin = True
    for vpath in args.vocab:
        try:
            extend_builtin = extend_builtin and _extend_builtin_flag(vpath)
            extra = _load_vocab_file(vpath)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if not extend_builtin:
            effective_signals = list(extra)
            extend_builtin = True  # only the FIRST file that sets false wins
        else:
            # id-level override: extra rules with the same id replace built-ins.
            ids_extra = {r.rule for r in extra}
            effective_signals = [s for s in effective_signals
                                 if s.rule not in ids_extra] + extra

    targets: list[Path] = []
    here = Path(__file__).resolve().parent
    pack_root = here.parent.parent
    if args.project:
        base = args.project.resolve()
        p = base / ".cursor" / "agents"
        if not p.exists():
            print(f"error: {p} does not exist -- nothing to scan",
                  file=sys.stderr)
            return 2
        targets = [p]
    elif args.path:
        targets = [p.resolve() for p in args.path]
        base = Path.cwd().resolve()
    else:
        targets = [pack_root / "agents"]
        base = pack_root

    try:
        findings, metas = scan(
            targets, base,
            stale_after_days=args.stale_after,
            require_version=args.require_version,
            signals=effective_signals,
        )
    except Exception as e:
        print(f"scan crashed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "targets":  [str(t) for t in targets],
            "findings": [asdict(f) for f in findings],
            "agents":   [asdict(m) for m in metas],
            "counts": {
                "error": sum(1 for f in findings if f.severity == "error"),
                "warn":  sum(1 for f in findings if f.severity == "warn"),
                "info":  sum(1 for f in findings if f.severity == "info"),
            },
        }, indent=2))
    else:
        print(render(findings, metas, verbose=args.verbose))

    n_errors = sum(1 for f in findings if f.severity == "error")
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
