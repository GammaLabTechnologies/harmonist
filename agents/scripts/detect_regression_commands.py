#!/usr/bin/env python3
"""
detect_regression_commands.py -- infer the test / lint / build / typecheck
commands a project uses by reading its manifest files. Used by `upgrade.py`
to auto-configure `bg-regression-runner.md` on fresh integrations so the
protocol gate runs real commands instead of silently no-oping.

Supported manifests (detected in this order; multiples are combined):

    package.json            npm / yarn / pnpm / vitest / jest / eslint / tsc / biome
    pnpm-lock.yaml          prefers `pnpm ...` over `npm ...`
    yarn.lock               prefers `yarn ...`
    pyproject.toml          pytest / ruff / mypy / pyright
    setup.cfg / tox.ini     pytest
    Cargo.toml              cargo test / cargo fmt --check / cargo clippy
    go.mod                  go test ./... / go vet ./... / gofmt -l
    pom.xml                 mvn -B test
    build.gradle(.kts)      ./gradlew test / check
    Makefile                make test / check / lint (if rule exists)
    composer.json           composer run test / stan / phpstan
    mix.exs                 mix test
    Gemfile                 bundle exec rspec / rubocop

Output: JSON with categories {"test": [...], "lint": [...], "typecheck": [...],
"build": [...]} or a text block for human reading.

Usage:
    detect_regression_commands.py [--project <path>] [--json]
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
from pathlib import Path


CATEGORIES = ("test", "lint", "typecheck", "build")


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _json_load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _js_runner(project: Path) -> str:
    """Pick between npm / yarn / pnpm / bun based on lockfiles."""
    if (project / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project / "yarn.lock").exists():
        return "yarn"
    if (project / "bun.lockb").exists():
        return "bun"
    return "npm"


def detect_js(project: Path) -> dict[str, list[str]]:
    pkg = _json_load(project / "package.json")
    if not pkg:
        return {}
    scripts = (pkg.get("scripts") or {}) if isinstance(pkg, dict) else {}
    runner = _js_runner(project)
    result: dict[str, list[str]] = {c: [] for c in CATEGORIES}

    def add(category: str, cmd: str) -> None:
        if cmd and cmd not in result[category]:
            result[category].append(cmd)

    # Prefer project-declared npm scripts first.
    for category_keys, category in [
        (("test",), "test"),
        (("lint",), "lint"),
        (("typecheck", "type-check", "types"), "typecheck"),
        (("build",), "build"),
    ]:
        for key in category_keys:
            if key in scripts:
                prefix = "run " if runner == "npm" else ""
                add(category, f"{runner} {prefix}{key}".strip())
                break

    # Fall back to tool-direct commands if the script block is silent.
    deps = (pkg.get("devDependencies") or {}) | (pkg.get("dependencies") or {})
    if not result["test"]:
        if "vitest" in deps:
            add("test", "npx vitest run")
        elif "jest" in deps:
            add("test", "npx jest --ci")
    if not result["lint"]:
        if "eslint" in deps:
            add("lint", "npx eslint .")
        if "@biomejs/biome" in deps or "biome" in deps:
            add("lint", "npx biome ci .")
    if not result["typecheck"]:
        if "typescript" in deps:
            add("typecheck", "npx tsc --noEmit")

    return result


def detect_python(project: Path) -> dict[str, list[str]]:
    result = {c: [] for c in CATEGORIES}
    pyproject = _read(project / "pyproject.toml")
    setup_cfg = _read(project / "setup.cfg")
    tox_ini = _read(project / "tox.ini")
    corpus = pyproject + setup_cfg + tox_ini

    if not corpus:
        return {}

    def add(category: str, cmd: str) -> None:
        if cmd and cmd not in result[category]:
            result[category].append(cmd)

    if "pytest" in corpus or "[tool.pytest.ini_options]" in corpus:
        add("test", "pytest -xvs")
    if "ruff" in corpus:
        add("lint", "ruff check .")
    if "mypy" in corpus:
        add("typecheck", "mypy .")
    if "pyright" in corpus:
        add("typecheck", "pyright")
    if "[tool.poetry]" in corpus:
        # poetry builds on `poetry build`, not usually part of regression gate
        pass
    return result


def detect_rust(project: Path) -> dict[str, list[str]]:
    if not (project / "Cargo.toml").exists():
        return {}
    return {
        "test": ["cargo test --all"],
        "lint": ["cargo clippy --all-targets -- -D warnings"],
        "typecheck": [],
        "build": ["cargo check --all-targets"],
    }


def detect_go(project: Path) -> dict[str, list[str]]:
    if not (project / "go.mod").exists():
        return {}
    return {
        "test": ["go test ./..."],
        "lint": ["go vet ./..."],
        "typecheck": [],
        "build": ["go build ./..."],
    }


def detect_jvm(project: Path) -> dict[str, list[str]]:
    result = {c: [] for c in CATEGORIES}
    if (project / "pom.xml").exists():
        result["test"] = ["mvn -B -ntp test"]
        result["build"] = ["mvn -B -ntp -DskipTests package"]
    elif (project / "build.gradle").exists() or (project / "build.gradle.kts").exists():
        wrapper = project / "gradlew"
        cmd = "./gradlew" if wrapper.exists() else "gradle"
        result["test"] = [f"{cmd} test"]
        result["lint"] = [f"{cmd} check"]
        result["build"] = [f"{cmd} assemble"]
    return result


def detect_make(project: Path) -> dict[str, list[str]]:
    mk = _read(project / "Makefile")
    if not mk:
        return {}
    # Capture top-level rule names.
    rules = set(re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]+)\s*:", mk, flags=re.MULTILINE))
    result: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for name, category in [("test", "test"), ("check", "lint"), ("lint", "lint"),
                           ("typecheck", "typecheck"), ("build", "build")]:
        if name in rules and f"make {name}" not in result[category]:
            result[category].append(f"make {name}")
    return result


def detect_misc(project: Path) -> dict[str, list[str]]:
    result = {c: [] for c in CATEGORIES}
    if (project / "composer.json").exists():
        pkg = _json_load(project / "composer.json")
        scripts = (pkg.get("scripts") or {}) if isinstance(pkg, dict) else {}
        if "test" in scripts:
            result["test"].append("composer run test")
        if "lint" in scripts:
            result["lint"].append("composer run lint")
        if "analyze" in scripts or "stan" in scripts:
            result["typecheck"].append("composer run stan" if "stan" in scripts else "composer run analyze")
    if (project / "mix.exs").exists():
        result["test"].append("mix test")
        result["lint"].append("mix credo --strict")
    if (project / "Gemfile").exists():
        gf = _read(project / "Gemfile")
        if "rspec" in gf:
            result["test"].append("bundle exec rspec")
        if "rubocop" in gf:
            result["lint"].append("bundle exec rubocop")
    if (project / "Dockerfile").exists() and not result["build"]:
        # Don't make Docker the default build -- CI slows down. Just hint.
        pass
    return result


DETECTORS = [detect_js, detect_python, detect_rust, detect_go, detect_jvm, detect_make, detect_misc]


def detect_all(project: Path) -> dict[str, list[str]]:
    combined: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for fn in DETECTORS:
        part = fn(project) or {}
        for cat, cmds in part.items():
            for c in cmds:
                if c and c not in combined[cat]:
                    combined[cat].append(c)
    return combined


def render_bg_regression(commands: dict[str, list[str]]) -> str:
    """Render the body block that replaces the template placeholders in
    `.cursor/agents/bg-regression-runner.md`.
    """
    any_command = any(commands.get(c) for c in CATEGORIES)
    if not any_command:
        return (
            "## Commands\n\n"
            "No manifest detected in this project -- replace this block with the\n"
            "exact commands this project uses for test / lint / typecheck / build.\n"
        )
    lines = ["## Commands\n",
             "Run these in order and stop on the first failure:\n"]
    labels = {"test": "Test", "lint": "Lint", "typecheck": "Typecheck", "build": "Build"}
    for cat in CATEGORIES:
        cmds = commands.get(cat) or []
        if not cmds:
            continue
        lines.append(f"### {labels[cat]}")
        for c in cmds:
            lines.append(f"    {c}")
        lines.append("")
    lines.append(
        "Return a concise failure-oriented report. Do not attempt fixes -- surface "
        "the first failure and the exact command line that produced it."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project root to scan. Default: current directory.")
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    ap.add_argument("--render", action="store_true",
                    help="Print the bg-regression-runner body block ready to drop in.")
    args = ap.parse_args(argv)

    commands = detect_all(args.project.resolve())

    if args.render:
        sys.stdout.write(render_bg_regression(commands))
        return 0
    if args.json:
        print(json.dumps({"project": str(args.project.resolve()), "commands": commands}, indent=2))
        return 0

    any_command = any(commands[c] for c in CATEGORIES)
    if not any_command:
        print(f"  (no recognisable manifest found under {args.project.resolve()})")
        return 1
    print(f"  detected commands for {args.project.resolve()}:")
    for cat in CATEGORIES:
        cmds = commands.get(cat) or []
        if not cmds:
            continue
        print(f"    {cat}:")
        for c in cmds:
            print(f"      {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
