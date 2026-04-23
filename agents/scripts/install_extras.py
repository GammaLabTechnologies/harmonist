#!/usr/bin/env python3
"""
install_extras.py -- add specialists to `.cursor/agents/` after integration.

Run from the target project's root (the same place where `AGENTS.md`
and `.cursor/` live):

    # Explicit slugs (comma-separated):
    python3 harmonist/agents/scripts/install_extras.py \
        --slug design-ux-architect,product-manager

    # Role bundle (applies Step 5 defaults from integration-prompt.md):
    python3 harmonist/agents/scripts/install_extras.py --role marketing

    # Tag filter (agent must match every requested tag):
    python3 harmonist/agents/scripts/install_extras.py --tag growth,seo

    # List what's installable (filtered by domain / role / tag):
    python3 harmonist/agents/scripts/install_extras.py --list [--role X]

    # Install the thin variant (essentials only, up to `## Deep Reference`):
    python3 harmonist/agents/scripts/install_extras.py --slug … --thin

Every install is sha-verified against the pack's `MANIFEST.sha256`
before copying. `.cursor/pack-manifest.json` is updated with the new
entries so `verify_integration.py` keeps tracking post-install drift.

Exit codes:
    0   dry-run / nothing to do / clean install
    1   installed something AND emitted warnings
    2   user-facing error (bad project, bad pack, unknown slug, refusal)
    3   Python version too old (handled by the guard below)
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
import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_AGENTS = SCRIPT_DIR.parent            # agents/
PACK_ROOT   = REPO_AGENTS.parent           # harmonist/

# --------------------------------------------------------------------------
# Role-default bundles. MUST match the table in integration-prompt.md
# (Step 5 — "Role-default specialist sets"). Keep these conservative: we
# pick what an orchestrator will actually dispatch when tasks in that
# role appear, not the entire category.
# --------------------------------------------------------------------------
ROLE_DEFAULTS: dict[str, list[str]] = {
    "engineering": [
        "engineering-backend-architect",
        "engineering-frontend-developer",
        "engineering-devops-automator",
    ],
    "design": [
        "design-ux-architect",
        "design-ui-designer",
        "design-visual-storyteller",
    ],
    "product": [
        "product-manager",
        "product-sprint-prioritizer",
        "product-feedback-synthesizer",
    ],
    "testing": [
        "testing-reality-checker",
        "testing-evidence-collector",
    ],
    "marketing": [
        "marketing-seo-specialist",
        "marketing-content-creator",
        "marketing-growth-hacker",
    ],
    "sales": [
        "sales-outbound-strategist",
        "sales-proposal-strategist",
    ],
    "support": [
        "support-support-responder",
        "support-analytics-reporter",
    ],
    "finance": [
        "finance-bookkeeper-controller",
        "finance-financial-analyst",
    ],
    "academic": [
        "academic-psychologist",
        "academic-anthropologist",
    ],
}

# Strict + orchestration slugs are owned by `upgrade.py --apply`;
# installing or overwriting them via this script is refused.
STRICT_SLUGS = frozenset({
    "repo-scout",
    "agents-orchestrator",
    "security-reviewer",
    "code-quality-auditor",
    "qa-verifier",
    "sre-observability",
    "bg-regression-runner",
    "wcag-a11y-gate",
})


@dataclass
class InstallOp:
    slug: str
    source: Path
    target: Path
    action: str                     # "copy" | "copy-thin" | "skip-exists" | "refused" | "dry-run"
    reason: str = ""


@dataclass
class InstallReport:
    project_root: Path
    pack_root: Path
    operations: list[InstallOp] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for op in self.operations:
            out[op.action] = out.get(op.action, 0) + 1
        return out


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_index(pack_root: Path) -> dict:
    idx = pack_root / "agents" / "index.json"
    if not idx.exists():
        raise FileNotFoundError(
            f"pack catalog missing: {idx} (run build_index.py from the pack root)"
        )
    return json.loads(idx.read_text())


def _load_manifest(pack_root: Path) -> dict[str, str]:
    """Parse MANIFEST.sha256 into {relative_path: sha}. Returns {} if absent."""
    p = pack_root / "MANIFEST.sha256"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            out[parts[1].strip()] = parts[0].strip().lower()
    return out


def _find_pack_root(explicit: Path | None) -> Path:
    if explicit:
        r = explicit.resolve()
        if not (r / "agents" / "index.json").exists():
            raise FileNotFoundError(f"{r} does not look like a pack root (no agents/index.json)")
        return r
    # Prefer the pack this script lives in.
    if (PACK_ROOT / "agents" / "index.json").exists():
        return PACK_ROOT
    # Fallback: cwd/harmonist
    cwd_pack = Path.cwd() / "harmonist"
    if (cwd_pack / "agents" / "index.json").exists():
        return cwd_pack
    raise FileNotFoundError(
        "cannot locate pack root; pass --pack <path-to-harmonist>"
    )


def _find_project_root(explicit: Path | None) -> Path:
    r = (explicit or Path.cwd()).resolve()
    if not (r / ".cursor" / "agents").exists():
        raise FileNotFoundError(
            f"{r} does not look like an integrated project — "
            f"`.cursor/agents/` is missing. "
            f"Run `upgrade.py --apply` from the project root first."
        )
    return r


def _agent_record(idx: dict, slug: str) -> dict | None:
    for a in idx.get("agents", []):
        if a.get("slug") == slug:
            return a
    return None


def _source_for(record: dict, pack_root: Path) -> Path:
    rel = record.get("path", "")
    src = pack_root / rel
    if not src.exists():
        raise FileNotFoundError(f"index points to missing file: {rel}")
    return src


def _domains_of(record: dict) -> set[str]:
    doms = record.get("domains") or ["all"]
    if isinstance(doms, str):
        doms = [d.strip() for d in doms.strip("[]").split(",") if d.strip()]
    return {str(d) for d in doms}


def _project_domains(project_root: Path) -> set[str]:
    """Best-effort read of project's declared `domains` list from AGENTS.md.
    If not found, default to {"all"} so nothing is filtered out."""
    p = project_root / "AGENTS.md"
    if not p.exists():
        return {"all"}
    text = p.read_text()
    # Scan for a "domains: [a, b, c]" style declaration in the first 200 lines.
    import re
    m = re.search(r"(?mi)^\s*(?:-\s*|\*\*)?domains\s*[:=]\s*\[([^\]]+)\]", text)
    if not m:
        return {"all"}
    return {d.strip().strip("`\"'") for d in m.group(1).split(",") if d.strip()}


# --------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------

def _candidates_by_slug(slugs: list[str], idx: dict) -> tuple[list[str], list[str]]:
    """Return (found_slugs, missing_slugs)."""
    pool = {a["slug"] for a in idx.get("agents", [])}
    found, missing = [], []
    for s in slugs:
        s = s.strip()
        if not s:
            continue
        if s in pool:
            found.append(s)
        else:
            missing.append(s)
    return found, missing


def _candidates_by_role(role: str, idx: dict) -> list[str]:
    """Role bundle (controlled list) -> slugs. If `role` maps to a
    category in index.by_category and no explicit bundle, fall back to
    the first 3 slugs from the category so the user gets *something*."""
    if role in ROLE_DEFAULTS:
        # Keep only slugs that exist (defensive — catalog may have
        # renamed something since ROLE_DEFAULTS was written).
        pool = {a["slug"] for a in idx.get("agents", [])}
        return [s for s in ROLE_DEFAULTS[role] if s in pool]
    cat_map = idx.get("by_category", {})
    if role in cat_map:
        return list(cat_map[role])[:3]
    return []


def _candidates_by_tags(tags: list[str], idx: dict, min_match: int) -> list[str]:
    tags_lc = [t.strip().lower() for t in tags if t.strip()]
    if not tags_lc:
        return []
    hits: dict[str, int] = {}
    for a in idx.get("agents", []):
        a_tags = {str(t).lower() for t in a.get("tags", [])}
        matched = sum(1 for t in tags_lc if t in a_tags)
        if matched >= min_match:
            hits[a["slug"]] = matched
    return sorted(hits, key=lambda s: (-hits[s], s))


def _filter_by_domains(slugs: list[str], idx: dict, project_domains: set[str]) -> list[str]:
    """Drop slugs whose domains don't intersect with project domains
    (treating "all" as universal). If the project declared no domains
    we treat the filter as transparent."""
    if not project_domains or project_domains == {"all"}:
        return slugs
    records = {a["slug"]: a for a in idx.get("agents", [])}
    out = []
    for s in slugs:
        rec = records.get(s)
        if rec is None:
            continue
        doms = _domains_of(rec)
        if "all" in doms or doms & project_domains:
            out.append(s)
    return out


# --------------------------------------------------------------------------
# Core install step
# --------------------------------------------------------------------------

def _install_one(
    slug: str,
    idx: dict,
    pack_root: Path,
    project_root: Path,
    manifest: dict[str, str],
    thin: bool,
    force: bool,
    dry_run: bool,
    report: InstallReport,
) -> None:
    if slug in STRICT_SLUGS:
        report.operations.append(InstallOp(
            slug=slug, source=Path(), target=Path(),
            action="refused",
            reason="strict/orchestration slug -- install via `upgrade.py --apply` instead",
        ))
        return

    record = _agent_record(idx, slug)
    if record is None:
        report.operations.append(InstallOp(
            slug=slug, source=Path(), target=Path(),
            action="refused",
            reason="slug not in agents/index.json",
        ))
        return

    try:
        src = _source_for(record, pack_root)
    except FileNotFoundError as e:
        report.operations.append(InstallOp(
            slug=slug, source=Path(), target=Path(),
            action="refused",
            reason=str(e),
        ))
        return

    target = project_root / ".cursor" / "agents" / f"{slug}.md"

    if target.exists() and not force:
        report.operations.append(InstallOp(
            slug=slug, source=src, target=target,
            action="skip-exists",
            reason="already installed (use --force to overwrite)",
        ))
        return

    # sha-verify source against MANIFEST (supply-chain guard).
    rel = str(src.resolve().relative_to(pack_root.resolve())).replace("\\", "/")
    expected = manifest.get(rel)
    actual = _sha256_of(src)
    if expected is not None and expected != actual:
        report.operations.append(InstallOp(
            slug=slug, source=src, target=target,
            action="refused",
            reason=(f"MANIFEST expected sha {expected[:12]}…, actual {actual[:12]}… "
                    f"-- possible supply-chain tampering"),
        ))
        return
    if expected is None:
        report.warnings.append(
            f"{slug}: source not in MANIFEST.sha256 "
            f"(regenerate with build_manifest.py)"
        )

    if dry_run:
        report.operations.append(InstallOp(
            slug=slug, source=src, target=target,
            action="dry-run",
            reason=("thin variant" if thin else "full body"),
        ))
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    if thin:
        # Use extract_essentials as a library.
        sys.path.insert(0, str(SCRIPT_DIR))
        from extract_essentials import extract  # noqa: E402
        result = extract(src)
        target.write_text(result.essentials_text)
        action = "copy-thin"
        extra = f"thin:{result.cut_reason}"
    else:
        shutil.copy2(src, target)
        action = "copy"
        extra = ""

    report.operations.append(InstallOp(
        slug=slug, source=src, target=target,
        action=action, reason=extra,
    ))


# --------------------------------------------------------------------------
# Manifest merge
# --------------------------------------------------------------------------

def _merge_pack_manifest(report: InstallReport, pack_version: str,
                          pack_root: Path, manifest: dict[str, str]) -> None:
    """Merge the new entries into `.cursor/pack-manifest.json` without
    clobbering entries that upgrade.py already recorded. Only files
    actually copied (not dry-run, skip, refused) are added."""
    pm_path = report.project_root / ".cursor" / "pack-manifest.json"
    payload: dict
    if pm_path.exists():
        try:
            payload = json.loads(pm_path.read_text())
        except Exception:
            payload = {}
    else:
        payload = {}

    files = payload.get("files")
    if not isinstance(files, dict):
        files = {}

    updated = False
    for op in report.operations:
        if op.action not in ("copy", "copy-thin"):
            continue
        rel_target = str(
            op.target.resolve().relative_to(report.project_root.resolve())
        ).replace("\\", "/")
        if op.action == "copy-thin":
            # Thin variants diverge from source sha; record the actual
            # file sha so verify_integration keeps a stable anchor.
            files[rel_target] = _sha256_of(op.target) + " (thin)"
        else:
            rel_src = str(
                op.source.resolve().relative_to(pack_root.resolve())
            ).replace("\\", "/")
            files[rel_target] = manifest.get(rel_src) or _sha256_of(op.source)
        updated = True

    if not updated:
        return

    payload.setdefault("pack_version", pack_version)
    payload["files"] = files
    payload["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pm_path.parent.mkdir(parents=True, exist_ok=True)
    pm_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# Listing mode
# --------------------------------------------------------------------------

def _print_listing(candidates: list[str], idx: dict, project_root: Path) -> None:
    installed = set()
    agents_dir = project_root / ".cursor" / "agents"
    if agents_dir.exists():
        installed = {p.stem for p in agents_dir.glob("*.md")}
    records = {a["slug"]: a for a in idx.get("agents", [])}
    print(f"  Candidates ({len(candidates)}) — '+' already installed, '-' available:\n")
    by_cat: dict[str, list[str]] = {}
    for s in candidates:
        rec = records.get(s)
        if not rec:
            continue
        by_cat.setdefault(rec.get("category", "?"), []).append(s)
    for cat in sorted(by_cat):
        print(f"  [{cat}]")
        for s in sorted(by_cat[cat]):
            mark = "+" if s in installed else "-"
            rec = records[s]
            desc = rec.get("description", "").strip()
            if len(desc) > 90:
                desc = desc[:87] + "..."
            print(f"    {mark} {s:<55} {desc}")
        print()


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------

def render_report(report: InstallReport, *, thin: bool, dry_run: bool) -> str:
    lines: list[str] = []
    c = report.counts()
    mode = "DRY-RUN" if dry_run else "APPLY"
    kind = "thin" if thin else "full"
    lines.append(f"  mode: {mode}   variant: {kind}")
    lines.append("")
    icon = {"copy": "+", "copy-thin": "+", "skip-exists": "=", "refused": "!", "dry-run": "?"}
    for op in report.operations:
        mark = icon.get(op.action, "?")
        extra = f"  ({op.reason})" if op.reason else ""
        lines.append(f"  {mark} {op.slug}{extra}")
    if not report.operations:
        lines.append("  (no candidates matched)")
    if report.warnings:
        lines.append("")
        lines.append("  warnings:")
        for w in report.warnings:
            lines.append(f"    ! {w}")
    if report.errors:
        lines.append("")
        lines.append("  errors:")
        for e in report.errors:
            lines.append(f"    X {e}")
    lines.append("")
    lines.append(
        "  summary: "
        + ", ".join(f"{k}={v}" for k, v in sorted(c.items()))
        + (f", warnings={len(report.warnings)}" if report.warnings else "")
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Install extra specialists into .cursor/agents/ after integration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--slug", help="Comma-separated list of slugs to install.")
    ap.add_argument(
        "--role",
        help=f"Role bundle. One of: {', '.join(sorted(ROLE_DEFAULTS))} "
             f"(or any category name from agents/index.json).",
    )
    ap.add_argument(
        "--tag",
        help="Comma-separated tag list. Install agents matching ALL tags "
             "(use --tag-min N to require only N intersections).",
    )
    ap.add_argument(
        "--tag-min", type=int, default=None,
        help="Minimum number of --tag intersections required (default: len(--tag))."
    )
    ap.add_argument("--list", action="store_true",
                    help="Print candidates and exit. Combine with --role / --tag to filter.")
    ap.add_argument("--thin", action="store_true",
                    help="Install the thin (essentials-only) variant.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing file in .cursor/agents/.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be installed without writing anything.")
    ap.add_argument("--project", type=Path, default=None,
                    help="Project root (default: cwd).")
    ap.add_argument("--pack", type=Path, default=None,
                    help="Pack root (default: the pack this script lives in).")
    ap.add_argument("--no-domain-filter", action="store_true",
                    help="Skip domain filtering (install regardless of project's "
                         "declared `domains`).")
    args = ap.parse_args(argv)

    if not any([args.slug, args.role, args.tag, args.list]):
        ap.error("pass at least one of --slug / --role / --tag / --list")

    # Resolve pack + project.
    try:
        pack_root = _find_pack_root(args.pack)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        project_root = _find_project_root(args.project)
    except FileNotFoundError as e:
        # --list from a non-integrated location is still useful (browse mode).
        if args.list:
            project_root = (args.project or Path.cwd()).resolve()
        else:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    idx = _load_index(pack_root)
    manifest = _load_manifest(pack_root)
    pack_version = (pack_root / "VERSION").read_text().strip() if (pack_root / "VERSION").exists() else ""

    # Assemble candidate list.
    candidates: list[str] = []
    unknown_slugs: list[str] = []
    if args.slug:
        found, missing = _candidates_by_slug(_parse_csv(args.slug), idx)
        candidates.extend(found)
        unknown_slugs.extend(missing)
    if args.role:
        bundle = _candidates_by_role(args.role, idx)
        if not bundle:
            print(
                f"ERROR: --role '{args.role}' is not a known role bundle "
                f"and not a category in agents/index.json.\n"
                f"Known role bundles: {', '.join(sorted(ROLE_DEFAULTS))}",
                file=sys.stderr,
            )
            return 2
        candidates.extend(bundle)
    if args.tag:
        tags = _parse_csv(args.tag)
        min_match = args.tag_min if args.tag_min is not None else len(tags)
        if min_match < 1:
            min_match = 1
        candidates.extend(_candidates_by_tags(tags, idx, min_match))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    candidates = [s for s in candidates if not (s in seen or seen.add(s))]

    # Domain filter.
    if not args.no_domain_filter:
        proj_domains = _project_domains(project_root)
        candidates = _filter_by_domains(candidates, idx, proj_domains)

    # List mode — print and return.
    if args.list:
        _print_listing(candidates, idx, project_root)
        return 0

    if unknown_slugs:
        print(f"WARNING: unknown slugs (not in index.json): {unknown_slugs}",
              file=sys.stderr)

    if not candidates:
        print("Nothing to install (candidate list is empty after filters).")
        return 0

    # Run installs.
    report = InstallReport(project_root=project_root, pack_root=pack_root)
    for s in candidates:
        _install_one(
            slug=s, idx=idx, pack_root=pack_root, project_root=project_root,
            manifest=manifest, thin=args.thin, force=args.force,
            dry_run=args.dry_run, report=report,
        )

    # Merge into pack-manifest.json on apply.
    if not args.dry_run:
        try:
            _merge_pack_manifest(report, pack_version, pack_root, manifest)
        except Exception as e:
            report.warnings.append(f"could not merge pack-manifest.json: {e}")

    print(render_report(report, thin=args.thin, dry_run=args.dry_run))

    c = report.counts()
    refused = c.get("refused", 0)
    installed = c.get("copy", 0) + c.get("copy-thin", 0)
    if refused and not installed:
        return 2
    if report.warnings and installed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
