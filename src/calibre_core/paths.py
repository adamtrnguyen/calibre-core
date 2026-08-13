"""Filesystem path -> catalogue record. The layout half of "what is a Calibre book".

Calibre files every book at `<root>/<Author>/<Title> (<id>)/<name>.<ext>`, and the
trailing `(<id>)` is the ONLY thing that maps a file on disk back to a row: the
filename is truncated (Calibre cuts the title at ~41 chars, which silently merges
books sharing a prefix) and the author folder is a sort name, so neither
identifies a record.

This module exists because that one fact was in service in THREE copies, each
with its own regex: `orphans` (id folders with no row), omni-rag's
`CalibreUuidResolver` (path -> uuid at ingest, which is what stamps calibre://
deep links onto search hits), and calibre-page-inserter's helper (the document
open in Skim -> the Calibre book). Three copies of a layout rule is three places
that do not get fixed when the layout changes.

The numeric `(id)` is library-local and shifts on rebuild/re-import; the `uuid`
does not. A path therefore resolves to a record HERE AND NOW — cache the uuid off
the returned record, never the id.
"""

from __future__ import annotations

import re
from pathlib import Path

from calibre_core.library import library_path
from calibre_core.records import Book, get_book

# Trailing "(123)" in a Calibre "Title (123)" book folder.
ID_SUFFIX = re.compile(r"\((\d+)\)$")


def book_id_from_dir(name: str) -> int | None:
    """The library-local book id in a Calibre book-folder NAME, or None.

    Takes a name rather than a path because both callers already have one (a
    directory entry while walking, a file's `parent.name`), and because passing a
    path invites `search()` against the whole string — where a title that itself
    ends in "(2)" two directories up would match.
    """
    m = ID_SUFFIX.search(name)
    return int(m.group(1)) if m else None


def library_root_for(path: str | Path) -> Path | None:
    """Nearest ancestor of `path` holding a metadata.db = a Calibre library root.

    For trees that are not the configured library: a Calibre export, or the
    staged copy of the library omni-rag ingests on HPC scratch, which carries its
    own metadata.db at the root. Returns None off a non-Calibre tree.
    """
    return next((p for p in Path(path).parents if (p / "metadata.db").exists()), None)


def resolve_path(
    path: str | Path,
    db: Path | None = None,
    *,
    discover_root: bool = False,
) -> Book | None:
    """The record whose book folder holds `path`, or None if there is none.

    None rather than an exception for every "not a book in this library" case —
    no `(id)` on the parent folder, a path outside the library, an `(id)` with no
    row. All three are ordinary answers for the callers that exist:
    calibre-page-inserter skips the foreign PDFs open alongside library books in
    Skim, and omni-rag's resolver is a documented no-op off a non-Calibre tree.
    (The third case is an orphan directory — `orphans.orphan_dirs` is what
    reports those; here it is just an unresolvable path.)

    Paths are compared RESOLVED ON BOTH SIDES. Callers hand us already-resolved
    paths — Skim reports the physical path of an open document — while
    `library_path()` is unresolved BY CONTRACT, because the library is a symlink
    into OneDrive. Comparing as-given makes every real lookup miss, which is why
    the plugin was calling `.resolve()` itself at its own comparison site. The
    library keeps its unresolved form for the returned Book's format paths, since
    that is what `orphans`' `relative_to` and every calibre:// link expect.

    `discover_root=True` takes the root from `library_root_for(path)` instead of
    `library_path()`. That is omni-rag's case, and the default cannot serve it:
    its ingest runs against a staged library copy that is not the configured
    library on the machine doing the ingesting. The trade is that "outside the
    library" stops being detectable — any tree with a metadata.db above the file
    resolves — so it is opt-in rather than a fallback. `db` still wins for which
    catalogue is read; the discovered root only supplies the format paths.
    """
    p = Path(path)
    book_id = book_id_from_dir(p.parent.name)
    if book_id is None:
        return None

    if discover_root:
        root = library_root_for(p)
        if root is None:
            return None
        return get_book(book_id, db or root / "metadata.db", root=root)

    root = library_path()
    physical, physical_root = p.resolve(), root.resolve()
    if physical_root not in physical.parents:
        return None
    return get_book(book_id, db, root=root)
