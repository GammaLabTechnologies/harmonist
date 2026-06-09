#!/usr/bin/env python3
"""
repomap.py -- a local, zero-dependency code map for the orchestrator.

Harmonist's answer to "stop grepping the same files every session". Builds a
pre-indexed map of the project's symbols (functions / classes / ...) and
file-level import dependencies into a local SQLite database, then answers
structural questions instantly:

  * search / explore  -> where a symbol is, its signature, grouped by file
                         (so repo-scout reads only what the map points at,
                         instead of grep + Read discovery loops)
  * deps / dependents -> a file's imports / importers (upstream / downstream)
  * impact            -> transitive blast radius of changing a set of files
  * affected          -> the impact set filtered to test files (run only the
                         tests a change can actually break)

Pure Python standard library only (`ast`, `re`, `sqlite3`) -- no tree-sitter,
no Node, no native build. Python files are parsed precisely with `ast`; other
languages use lightweight regex extraction (best-effort, like any name-based
indexer). Cross-platform: macOS / Linux / Windows.

The index lives at <project>/.cursor/repomap/graph.db and is gitignored.
`build` does a full index; `refresh` re-indexes only files whose content hash
changed (and drops deleted ones).

Usage:
    python3 repomap.py build      [--project P] [--json]
    python3 repomap.py refresh    [--project P] [--json]
    python3 repomap.py status     [--project P] [--json]
    python3 repomap.py search   <query>       [--project P] [--limit N] [--json]
    python3 repomap.py explore  <term...>     [--project P] [--limit N] [--json]
    python3 repomap.py deps       <file>      [--project P] [--json]
    python3 repomap.py dependents <file>      [--project P] [--json]
    python3 repomap.py impact   <file...>     [--project P] [--stdin] [--depth N] [--json]
    python3 repomap.py affected <file...>     [--project P] [--stdin] [--depth N] [--filter GLOB] [--json]

Exit codes:
    0 ok   1 nothing-found / not-built   2 usage / fatal
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
import ast
import fnmatch
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Language detection + extraction patterns
# ---------------------------------------------------------------------------

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift",
    ".scala": "scala", ".dart": "dart", ".lua": "lua",
}

SOURCE_EXTS = set(LANG_BY_EXT)

# Directories never walked. Mirrors the hooks' skip patterns + common vendor dirs.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "target", "coverage", ".next", ".nuxt", ".cursor",
    ".idea", ".vscode", "vendor", ".mypy_cache", ".pytest_cache", ".gradle",
    "Pods", "DerivedData", ".terraform", "bin", "obj",
}

# Regex symbol-definition patterns per language. Each yields a capture group
# named the symbol. Best-effort -- one line, no scope tracking. Python is
# handled by `ast` and is not in this table.
_DEF_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {}


def _c(rx: str) -> re.Pattern:
    return re.compile(rx)


def _register(langs: list[str], patterns: list[tuple[str, str]]) -> None:
    compiled = [(kind, _c(rx)) for kind, rx in patterns]
    for lang in langs:
        _DEF_PATTERNS[lang] = compiled


_register(["javascript", "typescript"], [
    ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)"),
    ("class",    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
    ("interface",r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"),
    ("type",     r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)"),
    ("enum",     r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)"),
    ("const",    r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
])
_register(["go"], [
    ("func", r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    ("type", r"^\s*type\s+([A-Za-z_]\w*)\s+"),
])
_register(["rust"], [
    ("fn",     r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),
    ("struct", r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?struct\s+([A-Za-z_]\w*)"),
    ("enum",   r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?enum\s+([A-Za-z_]\w*)"),
    ("trait",  r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?trait\s+([A-Za-z_]\w*)"),
])
_register(["java", "kotlin", "scala", "csharp"], [
    ("class",     r"^\s*(?:public|private|protected|internal|abstract|final|sealed|static|open|data|\s)*class\s+([A-Za-z_]\w*)"),
    ("interface", r"^\s*(?:public|private|protected|internal|\s)*interface\s+([A-Za-z_]\w*)"),
    ("enum",      r"^\s*(?:public|private|protected|internal|\s)*enum\s+(?:class\s+)?([A-Za-z_]\w*)"),
    ("fun",       r"^\s*(?:public|private|protected|internal|override|suspend|fun|def|static|final|\s)*\bfun\s+([A-Za-z_]\w*)"),
    ("def",       r"^\s*(?:public|private|protected|implicit|override|\s)*def\s+([A-Za-z_]\w*)"),
])
_register(["ruby"], [
    ("class",  r"^\s*class\s+([A-Z]\w*)"),
    ("module", r"^\s*module\s+([A-Z]\w*)"),
    ("def",    r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)"),
])
_register(["php"], [
    ("class",    r"^\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_]\w*)"),
    ("interface",r"^\s*interface\s+([A-Za-z_]\w*)"),
    ("trait",    r"^\s*trait\s+([A-Za-z_]\w*)"),
    ("function", r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|abstract\s+|final\s+)*function\s+([A-Za-z_]\w*)"),
])
_register(["c", "cpp"], [
    ("struct", r"^\s*(?:typedef\s+)?struct\s+([A-Za-z_]\w*)"),
    ("class",  r"^\s*class\s+([A-Za-z_]\w*)"),
])
_register(["swift"], [
    ("func",     r"^\s*(?:public|private|internal|fileprivate|open|static|class|final|override|\s)*func\s+([A-Za-z_]\w*)"),
    ("class",    r"^\s*(?:public|private|internal|fileprivate|open|final|\s)*class\s+([A-Za-z_]\w*)"),
    ("struct",   r"^\s*(?:public|private|internal|\s)*struct\s+([A-Za-z_]\w*)"),
    ("enum",     r"^\s*(?:public|private|internal|\s)*enum\s+([A-Za-z_]\w*)"),
    ("protocol", r"^\s*(?:public|private|internal|\s)*protocol\s+([A-Za-z_]\w*)"),
])
_register(["dart"], [
    ("class", r"^\s*(?:abstract\s+)?class\s+([A-Za-z_]\w*)"),
])
_register(["lua"], [
    ("function", r"^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)"),
])

# Import / dependency patterns. Each yields the raw specifier in group 1.
_IMPORT_PATTERNS: dict[str, list[re.Pattern]] = {
    "javascript": [
        _c(r"""import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""),
        _c(r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
        _c(r"""export\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]"""),
    ],
    "go": [_c(r'^\s*(?:[A-Za-z0-9_]+\s+)?"([^"]+)"')],
    "rust": [_c(r"^\s*(?:pub\s+)?use\s+([A-Za-z_][\w:]*)")],
    "java": [_c(r"^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")],
    "kotlin": [_c(r"^\s*import\s+([A-Za-z_][\w.]*)")],
    "scala": [_c(r"^\s*import\s+([A-Za-z_][\w.]*)")],
    "csharp": [_c(r"^\s*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")],
    "ruby": [_c(r"""^\s*(?:require|require_relative)\s+['"]([^'"]+)['"]""")],
    "php": [
        _c(r"^\s*use\s+([A-Za-z_][\w\\]*)"),
        _c(r"""^\s*(?:require|require_once|include|include_once)\s*\(?\s*['"]([^'"]+)['"]"""),
    ],
    "swift": [_c(r"^\s*import\s+([A-Za-z_]\w*)")],
    "dart": [_c(r"""^\s*import\s+['"]([^'"]+)['"]""")],
    "lua": [_c(r"""require\s*\(?\s*['"]([^'"]+)['"]""")],
    "c": [_c(r'^\s*#include\s+"([^"]+)"')],
    "cpp": [_c(r'^\s*#include\s+"([^"]+)"')],
}
_IMPORT_PATTERNS["typescript"] = _IMPORT_PATTERNS["javascript"]

# Default test-file detection (used by `affected`).
DEFAULT_TEST_PATTERNS = [
    _c(r"(^|/)tests?/"),
    _c(r"(^|/)__tests__/"),
    _c(r"(^|/)spec/"),
    _c(r"(?i)test[_.]"),
    _c(r"(?i)[_.]tests?\."),
    _c(r"(?i)[_.]spec\."),
    _c(r"_test\.go$"),
]

JS_RESOLVE_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts"]


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    lang TEXT NOT NULL,
    hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    symbol_count INTEGER DEFAULT 0,
    indexed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT,
    lang TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'import',
    UNIQUE(src, dst, kind)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_lname ON symbols(lower(name));
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
"""


def db_path(project: Path) -> Path:
    return project / ".cursor" / "repomap" / "graph.db"


def connect(project: Path, create: bool = False) -> "sqlite3.Connection | None":
    p = db_path(project)
    if not p.exists() and not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Walking + hashing
# ---------------------------------------------------------------------------

def _iter_source_files(project: Path):
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(project).parts)
        if parts & SKIP_DIRS:
            continue
        if path.suffix.lower() in SOURCE_EXTS:
            yield path


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _rel(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_python(text: str):
    """Return (symbols, raw_imports) using the real Python AST."""
    symbols: list[tuple] = []
    raw_imports: list[tuple[str, int]] = []  # (module-ish, level)
    try:
        tree = ast.parse(text)
    except Exception:
        return symbols, raw_imports

    def sig_of(node) -> str:
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = ""
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({args})"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(("function", node.name, node.lineno,
                            getattr(node, "end_lineno", node.lineno), sig_of(node)))
        elif isinstance(node, ast.ClassDef):
            symbols.append(("class", node.name, node.lineno,
                            getattr(node, "end_lineno", node.lineno), f"class {node.name}"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                raw_imports.append((a.name, 0))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            raw_imports.append((mod, node.level or 0))
    return symbols, raw_imports


def _extract_regex(lang: str, text: str):
    symbols: list[tuple] = []
    raw_imports: list[tuple[str, int]] = []
    def_patterns = _DEF_PATTERNS.get(lang, [])
    imp_patterns = _IMPORT_PATTERNS.get(lang, [])
    for i, line in enumerate(text.splitlines(), start=1):
        if len(line) > 400:
            line = line[:400]
        for kind, rx in def_patterns:
            m = rx.match(line)
            if m:
                symbols.append((kind, m.group(1), i, i, line.strip()[:200]))
                break
        for rx in imp_patterns:
            m = rx.search(line)
            if m:
                raw_imports.append((m.group(1), 0))
    return symbols, raw_imports


# ---------------------------------------------------------------------------
# Import resolution -> intra-repo file edges
# ---------------------------------------------------------------------------

def _resolve_python(spec: str, level: int, src_rel: str, all_files: set) -> "str | None":
    if level and level > 0:
        # Relative: climb `level` dirs from the importing file's package.
        base = Path(src_rel).parent
        for _ in range(level - 1):
            base = base.parent
        mod_path = (base / spec.replace(".", "/")) if spec else base
        cands = [f"{mod_path.as_posix()}.py", f"{mod_path.as_posix()}/__init__.py"]
        if not spec:
            cands = [f"{base.as_posix()}/__init__.py"]
        for c in cands:
            c = c.lstrip("/")
            if c in all_files:
                return c
        return None
    # Absolute module: map a.b.c -> a/b/c.py | a/b/c/__init__.py, anywhere.
    rel = spec.replace(".", "/")
    cands = [f"{rel}.py", f"{rel}/__init__.py"]
    for c in cands:
        if c in all_files:
            return c
    # Try under common source roots.
    for root in ("src/", "lib/", "app/"):
        for c in cands:
            if (root + c) in all_files:
                return root + c
    return None


def _resolve_relative_path(spec: str, src_rel: str, all_files: set) -> "str | None":
    """Resolve a JS/TS/dart/etc relative specifier ('./x', '../x') to a file."""
    if not (spec.startswith("./") or spec.startswith("../")):
        return None
    base = (Path(src_rel).parent / spec).as_posix()
    # Normalise '..' segments.
    parts: list[str] = []
    for seg in base.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg in ("", "."):
            continue
        else:
            parts.append(seg)
    norm = "/".join(parts)
    if norm in all_files:
        return norm
    for ext in JS_RESOLVE_EXTS:
        if norm + ext in all_files:
            return norm + ext
    for ext in JS_RESOLVE_EXTS:
        idx = f"{norm}/index{ext}"
        if idx in all_files:
            return idx
    return None


def _resolve_edge(lang: str, spec: str, level: int, src_rel: str, all_files: set) -> "str | None":
    if lang == "python":
        return _resolve_python(spec, level, src_rel, all_files)
    if lang in ("javascript", "typescript", "dart"):
        return _resolve_relative_path(spec, src_rel, all_files)
    if lang in ("c", "cpp"):
        # #include "x.h" -- relative to the including file.
        return _resolve_relative_path("./" + spec, src_rel, all_files) or (
            spec if spec in all_files else None)
    # Other languages: package-style imports rarely map 1:1 to a repo file;
    # skip (no intra-repo edge) rather than guess wrong.
    return None


# ---------------------------------------------------------------------------
# Build / refresh
# ---------------------------------------------------------------------------

def _index_one(conn, project: Path, path: Path, all_files: set) -> int:
    rel = _rel(project, path)
    lang = LANG_BY_EXT.get(path.suffix.lower(), "unknown")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    h = _hash(text)
    if lang == "python":
        symbols, raw_imports = _extract_python(text)
    else:
        symbols, raw_imports = _extract_regex(lang, text)

    conn.execute("DELETE FROM symbols WHERE file=?", (rel,))
    conn.execute("DELETE FROM edges WHERE src=?", (rel,))
    conn.executemany(
        "INSERT INTO symbols(name,kind,file,line,end_line,signature,lang) VALUES(?,?,?,?,?,?,?)",
        [(name, kind, rel, line, end, sig, lang) for (kind, name, line, end, sig) in symbols],
    )
    seen_edges = set()
    for spec, level in raw_imports:
        dst = _resolve_edge(lang, spec, level, rel, all_files)
        if dst and dst != rel and (rel, dst) not in seen_edges:
            seen_edges.add((rel, dst))
            conn.execute(
                "INSERT OR IGNORE INTO edges(src,dst,kind) VALUES(?,?,'import')", (rel, dst))
    conn.execute(
        "INSERT OR REPLACE INTO files(path,lang,hash,size,symbol_count,indexed_at) "
        "VALUES(?,?,?,?,?,?)",
        (rel, lang, h, len(text), len(symbols), int(time.time())),
    )
    return len(symbols)


def build(project: Path, incremental: bool) -> dict:
    conn = connect(project, create=True)
    assert conn is not None
    disk_files = list(_iter_source_files(project))
    all_files = {_rel(project, p) for p in disk_files}

    prev = {}
    force_full = False
    if incremental:
        for row in conn.execute("SELECT path, hash FROM files"):
            prev[row["path"]] = row["hash"]
        # Drop files that disappeared from disk.
        gone = set(prev) - all_files
        added = all_files - set(prev)
        for rel in gone:
            conn.execute("DELETE FROM symbols WHERE file=?", (rel,))
            conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (rel, rel))
            conn.execute("DELETE FROM files WHERE path=?", (rel,))
        # When the FILE SET changes (adds/removes), an UNCHANGED file's imports
        # can now resolve to a different target (a newly-added module becomes a
        # valid edge destination, or a removed one drops). Hash-skipping those
        # files would leave stale / missing cross-file edges, so re-resolve
        # every file's edges this pass. Pure content edits (same file set) keep
        # the fast incremental path.
        force_full = bool(gone or added)
    else:
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM files")

    indexed = 0
    skipped = 0
    total_symbols = 0
    for path in disk_files:
        rel = _rel(project, path)
        if incremental and not force_full and rel in prev:
            try:
                cur_hash = _hash(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                cur_hash = ""
            if cur_hash and cur_hash == prev[rel]:
                skipped += 1
                continue
        total_symbols += _index_one(conn, project, path, all_files)
        indexed += 1

    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('built_at',?)",
        (str(int(time.time())),))
    conn.commit()
    counts = _counts(conn)
    conn.close()
    return {"indexed": indexed, "skipped": skipped, "symbols_added": total_symbols, **counts}


def _counts(conn) -> dict:
    f = conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    s = conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"]
    e = conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
    return {"files": f, "symbols": s, "edges": e}


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def status(project: Path) -> dict:
    conn = connect(project)
    if conn is None:
        return {"built": False, "pending": 0,
                "hint": "run: python3 .cursor/repomap/repomap.py build"}
    counts = _counts(conn)
    built_at = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
    # Pending = files whose on-disk hash differs from the index (or new files).
    indexed = {row["path"]: row["hash"] for row in conn.execute("SELECT path, hash FROM files")}
    pending = []
    disk = {_rel(project, p): p for p in _iter_source_files(project)}
    for rel, p in disk.items():
        if rel not in indexed:
            pending.append(rel)
            continue
        try:
            if _hash(p.read_text(encoding="utf-8", errors="replace")) != indexed[rel]:
                pending.append(rel)
        except Exception:
            pass
    deleted = [r for r in indexed if r not in disk]
    conn.close()
    return {
        "built": True,
        "built_at": int(built_at["value"]) if built_at else None,
        "pending": len(pending) + len(deleted),
        "pending_files": sorted(pending)[:50],
        "deleted_files": sorted(deleted)[:50],
        **counts,
    }


def search(project: Path, query: str, limit: int) -> list[dict]:
    conn = connect(project)
    if conn is None:
        return []
    q = query.strip().lower()
    rows = conn.execute(
        "SELECT name,kind,file,line,signature,lang FROM symbols "
        "WHERE lower(name)=? ORDER BY name LIMIT ?", (q, limit)).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT name,kind,file,line,signature,lang FROM symbols "
            "WHERE lower(name) LIKE ? ORDER BY length(name), name LIMIT ?",
            (f"%{q}%", limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


_TOKEN_RE = re.compile(r"[A-Za-z_][\w]*")


def explore(project: Path, terms: list[str], limit: int) -> dict:
    """Map a question / bag of names to the relevant symbols, grouped by file.
    Returns the structural answer (kind + signature + location) so the caller
    Reads only what matters."""
    conn = connect(project)
    if conn is None:
        return {"built": False, "results": {}}
    # Tokenise: split each term, keep identifier-ish tokens >= 3 chars.
    tokens: list[str] = []
    for t in terms:
        for tok in _TOKEN_RE.findall(t):
            if len(tok) >= 3:
                tokens.append(tok.lower())
    tokens = list(dict.fromkeys(tokens))  # dedupe, keep order
    if not tokens:
        conn.close()
        return {"built": True, "results": {}, "tokens": []}

    scored: dict[int, int] = {}
    rowcache: dict[int, sqlite3.Row] = {}
    for tok in tokens:
        for r in conn.execute(
            "SELECT id,name,kind,file,line,signature,lang FROM symbols "
            "WHERE lower(name)=? OR lower(name) LIKE ?",
            (tok, f"%{tok}%")).fetchall():
            rid = r["id"]
            rowcache[rid] = r
            exact = 3 if r["name"].lower() == tok else 1
            scored[rid] = scored.get(rid, 0) + exact
    conn.close()

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], rowcache[kv[0]]["file"]))[:limit]
    grouped: dict[str, list[dict]] = {}
    for rid, _score in ranked:
        r = rowcache[rid]
        grouped.setdefault(r["file"], []).append({
            "name": r["name"], "kind": r["kind"], "line": r["line"],
            "signature": r["signature"], "lang": r["lang"],
        })
    for f in grouped:
        grouped[f].sort(key=lambda d: d["line"])
    return {"built": True, "tokens": tokens, "results": grouped}


def deps(project: Path, file: str) -> list[str]:
    conn = connect(project)
    if conn is None:
        return []
    rows = conn.execute("SELECT dst FROM edges WHERE src=? ORDER BY dst", (file,)).fetchall()
    conn.close()
    return [r["dst"] for r in rows]


def dependents(project: Path, file: str) -> list[str]:
    conn = connect(project)
    if conn is None:
        return []
    rows = conn.execute("SELECT src FROM edges WHERE dst=? ORDER BY src", (file,)).fetchall()
    conn.close()
    return [r["src"] for r in rows]


def impact(project: Path, files: list[str], depth: int) -> list[str]:
    """Transitive reverse-dependency closure: every file that (transitively)
    imports one of `files`. The blast radius of changing them."""
    conn = connect(project)
    if conn is None:
        return []
    # Build a reverse adjacency: dst -> [src...] (who imports dst).
    radj: dict[str, list[str]] = {}
    for r in conn.execute("SELECT src,dst FROM edges"):
        radj.setdefault(r["dst"], []).append(r["src"])
    conn.close()
    seen: set = set()
    frontier = set(_norm(f) for f in files)
    level = 0
    while frontier and level < depth:
        nxt: set = set()
        for f in frontier:
            for importer in radj.get(f, []):
                if importer not in seen:
                    seen.add(importer)
                    nxt.add(importer)
        frontier = nxt
        level += 1
    # Don't report the changed files themselves.
    for f in files:
        seen.discard(_norm(f))
    return sorted(seen)


def affected(project: Path, files: list[str], depth: int, test_globs: list[str]) -> list[str]:
    blast = impact(project, files, depth)
    # The changed files themselves can be tests too.
    candidates = sorted(set(blast) | {_norm(f) for f in files})
    out = []
    for f in candidates:
        if test_globs:
            if any(fnmatch.fnmatch(f, g) for g in test_globs):
                out.append(f)
        elif _looks_like_test(f):
            out.append(f)
    return sorted(out)


def _looks_like_test(path: str) -> bool:
    return any(rx.search(path) for rx in DEFAULT_TEST_PATTERNS)


def _norm(p: str) -> str:
    # Strip a leading './' prefix only -- NOT every leading '.'/'/'. Plain
    # str.lstrip("./") would corrupt '.github/workflows/ci.yml' -> 'github/...'
    # and '.eslintrc' -> 'eslintrc', so impact/affected lookups would miss the
    # very files the caller passed.
    s = p.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _read_stdin_files() -> list[str]:
    data = sys.stdin.read()
    return [ln.strip() for ln in data.splitlines() if ln.strip()]


def render_explore(payload: dict) -> str:
    if not payload.get("built"):
        return "repo map not built — run: python3 .cursor/repomap/repomap.py build"
    results = payload.get("results") or {}
    if not results:
        return f"no symbols matched {payload.get('tokens')}"
    lines = []
    for f in sorted(results):
        lines.append(f"{f}")
        for s in results[f]:
            sig = s["signature"] or s["name"]
            lines.append(f"  {s['line']:>5}  {s['kind']:<10} {sig}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=["build", "refresh", "status", "search",
                                     "explore", "deps", "dependents", "impact",
                                     "affected"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--filter", action="append", default=[],
                    help="glob(s) identifying test files for `affected`")
    ap.add_argument("--stdin", action="store_true",
                    help="read the file list from stdin (impact / affected)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        print(f"repomap: --project {project} is not a directory", file=sys.stderr)
        return 2

    verb = args.verb
    if verb in ("build", "refresh"):
        res = build(project, incremental=(verb == "refresh"))
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"repomap {verb}: indexed={res['indexed']} skipped={res['skipped']} "
                  f"files={res['files']} symbols={res['symbols']} edges={res['edges']}")
        return 0

    if verb == "status":
        res = status(project)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if not res.get("built"):
                print("repo map: NOT BUILT — " + res.get("hint", ""))
                return 1
            print(f"repo map: files={res['files']} symbols={res['symbols']} "
                  f"edges={res['edges']} pending={res['pending']}")
        return 0

    if verb == "search":
        rows = search(project, " ".join(args.args), args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            if not rows:
                print("no match")
                return 1
            for r in rows:
                print(f"{r['file']}:{r['line']}  {r['kind']:<10} {r['signature'] or r['name']}")
        return 0 if rows else 1

    if verb == "explore":
        payload = explore(project, args.args, args.limit)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(render_explore(payload))
        return 0 if payload.get("results") else 1

    if verb in ("deps", "dependents"):
        if not args.args:
            print(f"repomap {verb}: needs a file argument", file=sys.stderr)
            return 2
        fn = deps if verb == "deps" else dependents
        out = fn(project, _norm(args.args[0]))
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print("\n".join(out) if out else "(none)")
        return 0 if out else 1

    # impact / affected
    files = _read_stdin_files() if args.stdin else list(args.args)
    files = [_norm(f) for f in files]
    if not files:
        print(f"repomap {verb}: no files given", file=sys.stderr)
        return 2
    if verb == "impact":
        out = impact(project, files, args.depth)
    else:
        out = affected(project, files, args.depth, args.filter)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print("\n".join(out) if out else "(none)")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
