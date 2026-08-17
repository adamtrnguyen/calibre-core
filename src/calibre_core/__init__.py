"""The Calibre library, as a Python API. Every Calibre concern lives here.

Exists because the same logic was reimplemented across 15 files in 4 repos, with
THREE divergent title normalisers -- one of which stripped CJK characters, so
every CJK-titled book silently vanished from that repo's duplicate detection.
There was no single answer anywhere to "what is a Calibre record, and is it
valid." This is that answer:

  * ONE read-only connection chokepoint (`connect`) -- never a bare sqlite3.connect
  * ONE canonical record model (`Book`), with formats as absolute paths
  * ONE normaliser pair: `norm` for search reach, `dedup_key` for grouping
  * ONE gated write path (`writes`) -- GUI closed, DB backed up, duplicates
    detected before the record exists

Everything else is an INTERFACE over this package and holds no Calibre logic of
its own: the MCP server and `calfuzz` CLI in calibre-mcp, the Calibre plugin,
omni-rag's scripts. An interface shapes output and reports errors; it does not
decide what a duplicate is.

READS are read-only by construction -- `connect()` opens with `?mode=ro` and
cannot write, so that guarantee is a property of this package rather than of
whoever writes the next call site.

WRITES shell out to `calibredb` and NEVER issue SQL against metadata.db. That
distinction is the whole rule: Calibre maintains derived state (path layout,
search caches, link tables) that direct SQL desynchronises, so `calibredb` is
the sanctioned mutator. An earlier version of this docstring said writes did not
belong here at all, which over-read its own reason -- the argument is against raw
SQL, not against owning the gate. Keeping the gate outside meant every caller
re-implemented its preconditions, and they disagreed.
"""

from calibre_core.duplicates import (
    dupok_pairs,
    excused,
    excused_within,
    isbn_groups,
    sha256,
    size_groups,
    title_groups,
)
from calibre_core.isbn import (
    clean_isbn,
    hyphenate,
    to_isbn13,
    valid_isbn,
    valid_isbn10,
    valid_isbn13,
)
from calibre_core.library import (
    DEFAULT_LIBRARY,
    LibraryNotFound,
    SchemaError,
    connect,
    custom_column_id,
    db_path,
    library_path,
    schema_probe,
)
from calibre_core.normalize import CJK, author_surname, dedup_key, norm
from calibre_core.orphans import missing_formats, orphan_dirs, path_case_drift
from calibre_core.paths import book_id_from_dir, library_root_for, resolve_path
from calibre_core.records import Book, books_by_tag, get_book, iter_tags, load_books
from calibre_core.search import score, search, token_set_ratio
from calibre_core.toc import has_outline, inject_outline, sanitize_outline
from calibre_core.writes import (
    WriteBlocked,
    add_book,
    backup_db,
    calibredb_path,
    check_duplicate,
    current_identifiers,
    gui_is_open,
    reject_html_entities,
    remove_identifier,
    set_book_metadata,
)

__version__ = "0.6.0"

# `audit` and `openlibrary` are deliberately NOT re-exported here, and are
# reached as `from calibre_core.audit import ...`. They are report TOOLS with
# their own CLI subcommands, not primitives other code composes, and pulling them
# up would make every `import calibre_core` drag in `urllib.request`,
# `concurrent.futures` and a network client. The small import surface is a
# feature of this package, not an accident.

# Grouped by concern rather than sorted: the grouping is the documentation of
# what this package is for. RUF022 wants alphabetical, which would scatter it.
__all__ = [  # noqa: RUF022
    "__version__",
    # library access
    "DEFAULT_LIBRARY", "library_path", "db_path", "connect", "schema_probe",
    "custom_column_id", "LibraryNotFound", "SchemaError",
    # normalisation
    "CJK", "norm", "dedup_key", "author_surname",
    # records
    "Book", "load_books", "get_book", "books_by_tag", "iter_tags",
    # layout -- the "(id)" folder is the only path -> record mapping there is,
    # and it was reimplemented in three consumers before living here.
    "resolve_path", "book_id_from_dir", "library_root_for",
    # search
    "search", "score", "token_set_ratio",
    # duplicates -- `excused`/`excused_within` are public because they were not,
    # and two consumers reimplemented the pairwise check rather than import it.
    "title_groups", "size_groups", "isbn_groups", "dupok_pairs", "sha256",
    "excused", "excused_within",
    # writes -- gated; shell out to calibredb, never SQL
    "WriteBlocked", "gui_is_open", "backup_db", "calibredb_path",
    "check_duplicate", "add_book", "set_book_metadata",
    "current_identifiers", "remove_identifier",
    # public so a consumer building its own calibredb argv can apply the same
    # check: `&` is the author separator, so "&amp;" silently forks an author.
    "reject_html_entities",
    # table of contents -- the WRITE side only. Reading a PDF outline is a PDF
    # operation and lives with whoever parses PDFs; writing one mutates a file
    # Calibre owns, so it is gated here like every other write.
    "inject_outline", "sanitize_outline", "has_outline",
    # integrity
    "orphan_dirs", "missing_formats", "path_case_drift",
    # isbn -- to_isbn13 is what matches an ISBN-10 record against an ISBN-13 one,
    # so a consumer cannot dedupe across the two forms without it.
    "clean_isbn", "valid_isbn", "valid_isbn10", "valid_isbn13",
    "to_isbn13", "hyphenate",
]
