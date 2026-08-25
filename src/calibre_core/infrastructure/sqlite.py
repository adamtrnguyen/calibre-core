"""Locating the library and opening it — the single read-only chokepoint.

Every read in every consumer should come through `connect()`. Before this
existed, `?mode=ro` was copy-pasted at five sites in calibre-mcp and two more in
omni-rag's UUID resolver, and one script opened the same metadata.db with a bare
read-write `sqlite3.connect()` for a SELECT. Read-only-by-construction that
depends on whoever writes the next call site remembering is not a guarantee.

`connect()` is READ-ONLY and stays that way -- there is no write connection here
and there will not be one. The package's writes live in `writes.py` and shell out
to `calibredb`, because Calibre maintains derived state (path layout, search
caches, link tables) that direct SQL desynchronises. The rule is "no write ever
issues SQL against metadata.db", not "no writes in this package".
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / "Calibre Library"

# Tables and columns the core reads. `schema_probe` checks these so a Calibre
# upgrade that renames one fails loudly in a single place, rather than making
# seven call sites quietly return wrong answers.
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "books": ("id", "title", "path", "uuid", "timestamp", "pubdate", "last_modified"),
    "authors": ("id", "name"),
    "books_authors_link": ("book", "author"),
    "tags": ("id", "name"),
    "books_tags_link": ("book", "tag"),
    "data": ("book", "format", "name", "uncompressed_size"),
    "identifiers": ("book", "type", "val"),
    # `load_books` reads these, so they belong here by the rule above. Note there
    # is no `books.publisher` column -- the publisher lives ONLY in this pair of
    # tables, which is why a consumer that wanted it could not get it from `books`
    # and fell back to shelling out to `calibredb list --for-machine`.
    "publishers": ("id", "name"),
    "books_publishers_link": ("book", "publisher"),
}


class LibraryNotFound(Exception):
    """The library directory or its metadata.db is missing."""


class SchemaError(Exception):
    """metadata.db is present but does not look like the schema we read."""


def library_path() -> Path:
    """The library root.

    Reads the environment at CALL time, not import time — that is what makes
    `monkeypatch.setenv` work in tests, and it is the only injection seam the
    original code actually honoured.

    Returned unresolved. The real library is a symlink into OneDrive, and
    resolving here would change `Path.relative_to` output in orphan scanning; use
    `library_path().resolve()` explicitly if you need the physical path.
    """
    return Path(os.environ.get("CALIBRE_LIBRARY", str(DEFAULT_LIBRARY)))


def db_path() -> Path:
    return library_path() / "metadata.db"


def connect(db: Path | None = None) -> sqlite3.Connection:
    """Open metadata.db READ-ONLY. The only sanctioned way in."""
    p = db or db_path()
    if not p.exists():
        raise LibraryNotFound(f"no metadata.db at {p}")
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def schema_probe(db: Path | None = None) -> dict[str, list[str]]:
    """Assert the tables and columns we read exist. Returns what is missing.

    Empty dict means the schema is as expected. Raises LibraryNotFound if there
    is no database at all.
    """
    missing: dict[str, list[str]] = {}
    con = connect(db)
    try:
        present = {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, cols in REQUIRED_SCHEMA.items():
            if table not in present:
                missing[table] = ["<table absent>"]
                continue
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            gone = [c for c in cols if c not in have]
            if gone:
                missing[table] = gone
    finally:
        con.close()
    return missing


def custom_column_id(label: str, db: Path | None = None) -> int | None:
    """Resolve a custom column label (e.g. 'dupok') to its numeric id.

    Custom columns materialise as `custom_column_<id>` plus
    `books_custom_column_<id>_link`, so the id is needed to query them at all.
    """
    con = connect(db)
    try:
        row = con.execute(
            "SELECT id FROM custom_columns WHERE label = ?", (label,)
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()
