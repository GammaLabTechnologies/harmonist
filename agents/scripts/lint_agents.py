#!/usr/bin/env python3
"""
lint_agents.py — enforce Schema v2 across every agent markdown file.

Rules (see agents/SCHEMA.md):

  1. First line `---`, closing `---` present.
  2. All required frontmatter fields present and well-typed.
  3. `category` matches the parent directory name.
  4. Filename stem (slug — identity key) is unique across agents/ and
     matches `[a-z0-9][a-z0-9-]*`.
  5. `name` (frontmatter) is a non-empty string. It does NOT have to equal
     the slug — the slug is the identity key, `name` is a display label.
  6. `protocol` is `strict` or `persona`.
  7. `readonly` and `is_background` are booleans.
  8. `tags` is a non-empty list and contains no foreign-category names.
  9. Only keys defined in Schema v2 are present (no ad-hoc frontmatter keys).
 10. Body ≥ 50 words.

Exit code 0 = clean, 1 = errors found, 2 = bad invocation.
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

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from migrate_schema import (  # noqa: E402
    ALL_CATEGORIES,
    ALLOWED_DOMAINS,
    ALLOWED_KEYS,
    ALLOWED_TAGS,
    BOOL_FIELDS,
    CURRENT_SCHEMA_VERSION,
    KNOWN_SCHEMA_VERSIONS,
    MODEL_TIERS,
    NON_AGENT_DIRS,
    REPO_AGENTS,
    REQUIRED,
    STRICT_CATEGORIES,
    parse_frontmatter,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VALID_PROTOCOLS = {"strict", "persona"}

# distinguishes_from references resolve against the full agent pool.
# We collect them per file while scanning so the existence check can run
# once all slugs are known.
_deferred_peer_checks: list[tuple[str, str]] = []


def _closest(tag: str, pool: set[str], n: int = 3) -> list[str]:
    """Return up to `n` closest-matching tag names by prefix / substring."""
    tag_l = tag.lower()
    matches = sorted(
        pool,
        key=lambda t: (
            0 if t.startswith(tag_l) else (1 if tag_l in t else 2),
            abs(len(t) - len(tag_l)),
            t,
        ),
    )
    return matches[:n]


def check_file(path: Path, errors: list[str], warnings: list[str], slug_seen: dict[str, Path]) -> None:
    rel = str(path.relative_to(REPO_AGENTS.parent))
    raw = path.read_text()

    if not raw.startswith("---\n"):
        errors.append(f"{rel}: missing opening frontmatter delimiter")
        return
    end = raw.find("\n---\n", 4)
    if end == -1:
        errors.append(f"{rel}: missing closing frontmatter delimiter")
        return
    fm_raw = raw[4:end]
    body = raw[end + 5 :]

    fields, _ = parse_frontmatter(fm_raw)

    # Required fields
    for key in REQUIRED:
        if key not in fields:
            errors.append(f"{rel}: missing required frontmatter field '{key}'")

    # Schema version must be known AND match the current. Stale files are
    # rejected with a clear pointer to the migrator -- no silent drift.
    sv = str(fields.get("schema_version") or "").strip()
    if sv and sv not in KNOWN_SCHEMA_VERSIONS:
        errors.append(
            f"{rel}: schema_version={sv!r} is unknown. "
            f"Known versions: {sorted(KNOWN_SCHEMA_VERSIONS)}. "
            f"If this is a newer pack, upgrade the tooling."
        )
    elif sv and sv != CURRENT_SCHEMA_VERSION:
        errors.append(
            f"{rel}: schema_version={sv!r} is older than the current "
            f"{CURRENT_SCHEMA_VERSION!r}. Run "
            f"'python3 agents/scripts/migrate_schema.py' to roll it forward."
        )

    # Only allowed keys
    for key in fields:
        if key not in ALLOWED_KEYS:
            errors.append(
                f"{rel}: non-schema frontmatter key '{key}' "
                f"(allowed keys: {sorted(ALLOWED_KEYS)})"
            )

    # Category = first directory under agents/. Sub-folders inside a category
    # (e.g. game-development/unity/) are permitted as organizational groupings.
    try:
        rel_from_agents = path.relative_to(REPO_AGENTS)
        expected_category = rel_from_agents.parts[0]
    except ValueError:
        expected_category = path.parent.name
    if "category" in fields and fields["category"] != expected_category:
        errors.append(
            f"{rel}: frontmatter category='{fields['category']}' "
            f"does not match root category directory '{expected_category}'"
        )
    if expected_category not in ALL_CATEGORIES:
        errors.append(
            f"{rel}: root directory '{expected_category}' is not a known category"
        )

    # Slug uniqueness + shape. The slug is the filename stem and the
    # authoritative identity key (routing, hooks, memory, telemetry).
    slug = path.stem
    if not SLUG_RE.match(slug):
        errors.append(f"{rel}: slug '{slug}' must match [a-z0-9][a-z0-9-]*")
    if slug in slug_seen:
        errors.append(
            f"{rel}: duplicate slug '{slug}' (also in {slug_seen[slug].relative_to(REPO_AGENTS.parent)})"
        )
    else:
        slug_seen[slug] = path

    # `name` is the human-readable display name. It does NOT have to equal
    # the slug (strict reviewers conventionally match, persona agents
    # commonly use prose form), but it MUST be a non-empty string.
    raw_name = fields.get("name")
    if "name" in fields:
        if not isinstance(raw_name, str) or not raw_name.strip():
            errors.append(
                f"{rel}: name must be a non-empty string (got {raw_name!r})"
            )

    # Protocol enum
    if "protocol" in fields and fields["protocol"] not in VALID_PROTOCOLS:
        errors.append(
            f"{rel}: protocol='{fields['protocol']}' must be one of {sorted(VALID_PROTOCOLS)}"
        )

    # Model tier enum
    model = fields.get("model")
    if model is not None and model not in MODEL_TIERS:
        errors.append(
            f"{rel}: model='{model}' must be one of {sorted(MODEL_TIERS)}"
        )

    # strict invariants: orchestration + review agents should be readonly
    if (
        fields.get("protocol") == "strict"
        and fields.get("readonly") is False
        and expected_category in STRICT_CATEGORIES
    ):
        warnings.append(
            f"{rel}: protocol=strict in category '{expected_category}' "
            f"but readonly=false — review intended"
        )

    # Bool fields
    for bf in BOOL_FIELDS:
        if bf in fields and not isinstance(fields[bf], bool):
            errors.append(f"{rel}: {bf}='{fields[bf]}' must be boolean true/false")

    # Tags
    tags = fields.get("tags")
    if tags is None or not isinstance(tags, list) or not tags:
        errors.append(f"{rel}: tags must be a non-empty list")
    elif ALLOWED_TAGS:
        pool = set(ALLOWED_TAGS.keys())
        seen_tags: set[str] = set()
        for t in tags:
            t = str(t).strip().lower()
            if t in seen_tags:
                errors.append(f"{rel}: duplicate tag '{t}'")
                continue
            seen_tags.add(t)
            if t in ALL_CATEGORIES and t != expected_category:
                errors.append(
                    f"{rel}: tag '{t}' names a foreign category (must be the agent's own)"
                )
                continue
            if t not in pool:
                suggest = _closest(t, pool)
                hint = f" closest vocabulary matches: {', '.join(suggest)}" if suggest else ""
                errors.append(
                    f"{rel}: tag '{t}' is not in the curated vocabulary (agents/tags.json)."
                    f"{hint}"
                )

    # domains — if present, every entry must be in the controlled vocab
    doms = fields.get("domains")
    if doms is not None:
        if not isinstance(doms, list):
            errors.append(f"{rel}: domains must be a list")
        else:
            for d in doms:
                d = str(d).strip().lower()
                if not d:
                    continue
                if d not in ALLOWED_DOMAINS:
                    suggest = _closest(d, ALLOWED_DOMAINS)
                    hint = f" (closest known domains: {', '.join(suggest)})" if suggest else ""
                    errors.append(
                        f"{rel}: domain '{d}' is not in the controlled vocabulary"
                        f"{hint}"
                    )

    # distinguishes_from — every referenced slug must exist AND not be self
    dfrom = fields.get("distinguishes_from")
    if dfrom is not None:
        if not isinstance(dfrom, list):
            errors.append(f"{rel}: distinguishes_from must be a list")
        else:
            seen_peers: set[str] = set()
            for peer in dfrom:
                peer = str(peer).strip()
                if not peer:
                    continue
                if peer == slug:
                    errors.append(f"{rel}: distinguishes_from cannot reference self ('{peer}')")
                if peer in seen_peers:
                    errors.append(f"{rel}: duplicate peer '{peer}' in distinguishes_from")
                seen_peers.add(peer)
                # Existence check happens after slug_seen is fully populated;
                # mark for deferred verification.
                _deferred_peer_checks.append((rel, peer))

    # Optional: version (SemVer string), updated_at (ISO-8601 date),
    # deprecated (bool or string reason). Used by freshness tooling.
    ver = fields.get("version")
    if ver is not None:
        if not isinstance(ver, str):
            errors.append(f"{rel}: version must be a string (SemVer)")
        elif not re.match(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$", ver):
            errors.append(f"{rel}: version {ver!r} is not a SemVer string")

    ua = fields.get("updated_at")
    if ua is not None:
        if not isinstance(ua, str):
            errors.append(f"{rel}: updated_at must be a string (YYYY-MM-DD)")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?Z?)?$", ua):
            errors.append(
                f"{rel}: updated_at {ua!r} must be YYYY-MM-DD or ISO-8601"
            )

    dep = fields.get("deprecated")
    if dep is not None and not isinstance(dep, (bool, str)):
        errors.append(f"{rel}: deprecated must be a bool or a string reason")

    # disambiguation length
    disambig = fields.get("disambiguation")
    if isinstance(disambig, str):
        if len(disambig) > 240:
            errors.append(f"{rel}: disambiguation is {len(disambig)} chars; max 240")
        if "\n" in disambig:
            errors.append(f"{rel}: disambiguation must be a single line")
    elif disambig is not None and disambig != "":
        errors.append(f"{rel}: disambiguation must be a string")

    # Body length
    word_count = len(body.split())
    if word_count < 50:
        warnings.append(f"{rel}: body is very short ({word_count} words; minimum 50)")

    # Persona bodies longer than 200 non-blank lines should declare a
    # `## Deep Reference` cut point so the extractor can ship a thin
    # variant. Warn only; agents opt in over time.
    if fields.get("protocol") == "persona":
        non_blank_body_lines = sum(1 for l in body.splitlines() if l.strip())
        has_deep_ref = any(l.strip() == "## Deep Reference" for l in body.splitlines())
        if non_blank_body_lines > 200 and not has_deep_ref:
            warnings.append(
                f"{rel}: persona body is {non_blank_body_lines} non-blank lines "
                f"and has no '## Deep Reference' cut point. Thin variants will "
                f"fall back to the budget heuristic."
            )

        # Persona agents MUST carry the project-precedence marker so the
        # subagent sees the authoritative rules when invoked. The migrator
        # inserts it automatically; this lint catches hand-edits that drop it.
        if "<!-- precedence: project-agents-md -->" not in body:
            warnings.append(
                f"{rel}: persona body missing project-precedence marker "
                f"'<!-- precedence: project-agents-md -->'. Run "
                f"'python3 agents/scripts/migrate_schema.py' to restore."
            )


def collect_files(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a).resolve() for a in argv]
    files: list[Path] = []
    for cat_dir in sorted(REPO_AGENTS.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in NON_AGENT_DIRS or cat_dir.name.startswith("."):
            continue
        files.extend(sorted(cat_dir.rglob("*.md")))
    return files


def main(argv: list[str]) -> int:
    files = collect_files(argv)
    if not files:
        print("No agent files found.", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []
    slug_seen: dict[str, Path] = {}
    _deferred_peer_checks.clear()

    for p in files:
        if not p.is_file():
            errors.append(f"{p}: not a file")
            continue
        check_file(p, errors, warnings, slug_seen)

    # Deferred: resolve distinguishes_from references against the fully
    # populated slug pool. Missing / typo'd peers would otherwise silently
    # neuter the orchestrator's tie-breaker.
    for rel, peer in _deferred_peer_checks:
        if peer not in slug_seen:
            suggest = _closest(peer, set(slug_seen.keys()))
            hint = f" (closest known slugs: {', '.join(suggest)})" if suggest else ""
            errors.append(f"{rel}: distinguishes_from references unknown slug '{peer}'{hint}")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    print("")
    print(
        f"Linted {len(files)} agent file(s): "
        f"{len(errors)} error(s), {len(warnings)} warning(s)."
    )
    if errors:
        print("FAILED")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
