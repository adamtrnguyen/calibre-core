"""Shared read-only access to a Calibre library.

Exists because the same logic was reimplemented across 15 files in 4 repos, with
THREE divergent title normalisers -- one of which stripped CJK characters, so
every CJK-titled book silently vanished from that repo's duplicate detection.
There was no single answer anywhere to "what is a Calibre record, and is it
valid." This is that answer:

  * ONE read-only connection chokepoint (`connect`) -- never a bare sqlite3.connect
  * ONE canonical record model (`Book`), with formats as absolute paths
  * ONE normaliser pair: `norm` for search reach, `dedup_key` for grouping

Writes are NOT here and will not be added. They belong in calling code via
calibredb / calibre-debug new_api, because Calibre maintains derived state (path
layout, search caches, link tables) that direct SQL desynchronises.
"""

from calibre_core.duplicates import (
    dupok_pairs,
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
from calibre_core.records import Book, books_by_tag, get_book, iter_tags, load_books
from calibre_core.search import score, search, token_set_ratio

__version__ = "0.1.0"

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
    # search
    "search", "score", "token_set_ratio",
    # duplicates
    "title_groups", "size_groups", "isbn_groups", "dupok_pairs", "sha256",
    # integrity
    "orphan_dirs", "missing_formats", "path_case_drift",
    # isbn -- to_isbn13 is what matches an ISBN-10 record against an ISBN-13 one,
    # so a consumer cannot dedupe across the two forms without it.
    "clean_isbn", "valid_isbn", "valid_isbn10", "valid_isbn13",
    "to_isbn13", "hyphenate",
]
