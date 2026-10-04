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
import tomllib
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


# Where the library and its database live, for these tools. Optional: with no
# file, the environment / macos-env.txt / defaults below decide.
#
#   [library]
#   root = "~/Calibre Library"                                  # book files
#   db   = "~/Library/Application Support/CalibreDB/metadata.db"  # local SQLite
CONFIG_FILE = Path.home() / ".config" / "calibre-core" / "config.toml"

# Calibre reads this file at launch for every binary in calibre.app (GUI, calibredb,
# calibre-server). It is the fallback for the db location, so that with no
# config.toml these tools still agree with Calibre about where metadata.db lives.
MACOS_ENV_FILE = Path.home() / "Library" / "Preferences" / "calibre" / "macos-env.txt"
OVERRIDE_VAR = "CALIBRE_OVERRIDE_DATABASE_PATH"


def _config() -> dict[str, str]:
    """The [library] table of config.toml, read at CALL time (tests patch CONFIG_FILE)."""
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("rb") as fh:
        return tomllib.load(fh).get("library", {})


def configured_library() -> Path:
    """The library these tools are configured for: config.toml `root`, else the default."""
    root = _config().get("root")
    return Path(root).expanduser() if root else DEFAULT_LIBRARY


def library_path() -> Path:
    """The library root: $CALIBRE_LIBRARY, else config.toml `root`, else the default.

    Reads at CALL time, not import time -- that is what makes `monkeypatch.setenv`
    work in tests, and it is the only injection seam the original code honoured.

    Returned unresolved. The real library is a symlink to the NAS
    (`/Volumes/NFS_Store/Calibre Library`), and resolving here would change
    `Path.relative_to` output in orphan scanning; use `library_path().resolve()`
    explicitly if you need the physical path.
    """
    env = os.environ.get("CALIBRE_LIBRARY")
    return Path(env) if env else configured_library()


def override_db_path() -> Path | None:
    """Where metadata.db lives when it is NOT at the library root, else None.

    The book files live on the NAS and metadata.db stays on local disk, because
    SQLite over a network filesystem is unsafe (Calibre FAQ: "Do not put your
    calibre library on a networked drive"; Calibre's escape hatch is
    CALIBRE_OVERRIDE_DATABASE_PATH). Order: the process environment, then
    config.toml `db`, then Calibre's macos-env.txt.
    """
    val = os.environ.get(OVERRIDE_VAR) or _config().get("db")
    if not val and MACOS_ENV_FILE.exists():
        for line in MACOS_ENV_FILE.read_text().splitlines():
            key, sep, rest = line.partition("=")
            if sep and key.strip() == OVERRIDE_VAR:
                val = rest.strip()
    return Path(val).expanduser() if val else None


def db_for(library: Path) -> Path:
    """metadata.db for a library root.

    An override in the process environment always wins (Calibre does the same).
    One from config.toml or macos-env.txt applies to the configured library alone,
    so a test fixture, a Calibre export or the staged HPC copy -- each carrying
    its own metadata.db at its root -- is never silently redirected to the real
    catalogue.
    """
    override = override_db_path()
    if override and (
        OVERRIDE_VAR in os.environ
        or library.resolve() == configured_library().resolve()
    ):
        return override
    return library / "metadata.db"


def db_path() -> Path:
    return db_for(library_path())


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
