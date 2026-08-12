"""Integrity between the catalogue and the filesystem — in BOTH directions.

The original check was one-directional (a directory with no DB row), so it could
not see a row whose directory had vanished, a format row with no file, or
case-only path drift. That last one is the nastiest: on case-insensitive APFS
`JOHN MONTAGUE/` satisfies a lookup for `John Montague/`, the GUI shows the book,
`os.path.isdir()` returns True — and the file becomes unreachable the moment the
library is rsynced to a case-sensitive target (NAS, HPC scratch, Linux).

`.caltrash` is excluded deliberately: it holds deleted books, so walking it
reports them as orphans and, worse, hydrates discarded books from OneDrive.
"""

from __future__ import annotations

import re
from pathlib import Path

from calibre_core.library import connect, library_path

BOOK_SUFFIXES = (".pdf", ".epub", ".djvu", ".mobi", ".azw3", ".cbz", ".cbr", ".txt")
_ID_SUFFIX = re.compile(r"\((\d+)\)$")
EXCLUDED_DIRS = {".caltrash", ".calnotes"}


def orphan_dirs(db: Path | None = None) -> list[dict]:
    """Directories holding a book file with no matching row in metadata.db.

    These appear after a metadata.db rollback: the catalogue reverts, the
    filesystem does not. Invisible to the GUI and to `calibredb list`, yet a
    directory walk finds them — so omni-rag ingests them while the UUID resolver
    returns None, silently costing those books their calibre:// links.
    """
    lib = library_path()
    con = connect(db)
    try:
        have = {r[0] for r in con.execute("SELECT id FROM books")}
    finally:
        con.close()
    out = []
    for d in sorted(lib.glob("*/*")):
        if not d.is_dir() or set(d.parts) & EXCLUDED_DIRS:
            continue
        m = _ID_SUFFIX.search(d.name)
        if not m or int(m.group(1)) in have:
            continue
        files = [f.name for f in d.iterdir() if f.suffix.lower() in BOOK_SUFFIXES]
        out.append(
            {"id": int(m.group(1)), "path": str(d.relative_to(lib)), "files": files}
        )
    return out


def missing_formats(db: Path | None = None) -> list[dict]:
    """Format rows whose file is not on disk — the reverse of an orphan."""
    from calibre_core.records import load_books

    out = []
    for b in load_books(db):
        for p in b.formats:
            if not p.exists():
                out.append({"id": b.id, "title": b.title, "missing": str(p)})
    return out


def path_case_drift(db: Path | None = None) -> list[dict]:
    """Rows whose `books.path` differs from the on-disk name only by case.

    Compared against `os.listdir` of the parent, NOT `Path.exists()` — on APFS
    `exists()` returns True for the wrong case, which is exactly what hides this
    until an rsync to a case-sensitive filesystem breaks the link.
    """
    lib = library_path()
    con = connect(db)
    try:
        rows = con.execute("SELECT id, title, path FROM books").fetchall()
    finally:
        con.close()
    out = []
    for bid, title, rel in rows:
        parts = Path(rel).parts
        if not parts:
            continue
        cur = lib
        for part in parts:
            if not cur.is_dir():
                break
            names = {p.name for p in cur.iterdir()}
            if part in names:
                cur = cur / part
                continue
            ci = [n for n in names if n.casefold() == part.casefold()]
            if ci:
                out.append(
                    {"id": bid, "title": title, "expected": part, "on_disk": ci[0],
                     "parent": str(cur.relative_to(lib)) or "."}
                )
            break
    return out
