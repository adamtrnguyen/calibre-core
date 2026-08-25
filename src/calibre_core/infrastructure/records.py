"""Queries that build Book records from metadata.db.

`formats` carries ABSOLUTE PATHS, not format codes. The two shapes existed in
different repos — calibre-mcp's get_book returned `data.format` strings while
omni-rag's audit needed real paths — and reconciling them is the one genuinely
fiddly part of this extraction. Paths win because a path can always be reduced to
its suffix, while a code cannot be turned back into a path without re-querying.
"""

from __future__ import annotations

from pathlib import Path

from calibre_core.domain.book import Book, split_field
from calibre_core.infrastructure.sqlite import connect, library_path

# There is NO `books.publisher` column -- checked against the real Calibre 9.x
# schema, which is `publishers(id, name, sort, link)` plus
# `books_publishers_link(id, book, publisher)`. That link table carries
# `UNIQUE(book)`, so a book has AT MOST ONE publisher and a scalar subquery is
# exactly right; GROUP_CONCAT here would imply a fan-out that cannot happen.
_BOOKS_SQL = """
SELECT b.id, b.title, b.path, b.uuid, b.timestamp, b.pubdate, b.last_modified,
       (SELECT GROUP_CONCAT(a.name, ' & ')
          FROM books_authors_link al JOIN authors a ON a.id = al.author
         WHERE al.book = b.id),
       (SELECT GROUP_CONCAT(t.name, ',')
          FROM books_tags_link tl JOIN tags t ON t.id = tl.tag
         WHERE tl.book = b.id),
       (SELECT val FROM identifiers WHERE book = b.id AND type = 'isbn' LIMIT 1),
       (SELECT p.name
          FROM books_publishers_link pl JOIN publishers p ON p.id = pl.publisher
         WHERE pl.book = b.id)
FROM books b
"""


def _query(db: Path | None, root: Path, book_id: int | None = None) -> list[Book]:
    """The one place a Book is built from rows. `book_id` narrows both queries.

    One query for books plus one for formats — never a single join across both
    the authors and formats fan-outs, which multiplies rows. (And
    `GROUP_CONCAT(DISTINCT x, sep)` is a syntax error in SQLite: DISTINCT
    aggregates take exactly one argument.)
    """
    where = " WHERE b.id = ?" if book_id is not None else ""
    args: tuple = (book_id,) if book_id is not None else ()
    con = connect(db)
    try:
        rows = con.execute(_BOOKS_SQL + where, args).fetchall()
        fmts: dict[int, list[tuple[Path, int]]] = {}
        paths = {r[0]: r[2] for r in rows}
        for bid, name, fmt, size in con.execute(
            "SELECT book, name, format, uncompressed_size FROM data"
            + (" WHERE book = ?" if book_id is not None else ""),
            args,
        ):
            if bid in paths:
                fmts.setdefault(bid, []).append(
                    (root / paths[bid] / f"{name}.{str(fmt).lower()}", int(size or 0))
                )
    finally:
        con.close()

    out: list[Book] = []
    for (bid, title, path, uuid, ts, pub, lastmod, authors, tags, isbn, publisher) in rows:
        pairs = fmts.get(bid, [])
        out.append(
            Book(
                id=bid,
                title=title or "",
                authors=split_field(authors, "&"),
                tags=split_field(tags, ","),
                formats=tuple(p for p, _ in pairs),
                sizes=tuple(s for _, s in pairs),
                isbn=isbn,
                path=path or "",
                uuid=uuid,
                timestamp=ts,
                pubdate=pub,
                last_modified=lastmod,
                publisher=publisher,
            )
        )
    return out


def load_books(db: Path | None = None, *, root: Path | None = None) -> list[Book]:
    """Every book, with formats resolved to absolute paths under `root`.

    `root` defaults to `library_path()` and is what every format path hangs off.
    Pass it only when reading a catalogue that is NOT the configured library — a
    Calibre export, or the staged copy omni-rag ingests on HPC scratch — where
    `library_path()` would point every format at a tree the db knows nothing
    about. `db` alone is not enough for that: it selects the catalogue, not the
    files.
    """
    return _query(db, root or library_path())


def get_book(book_id: int, db: Path | None = None, *, root: Path | None = None) -> Book | None:
    """One book by id. Queried directly rather than filtering `load_books`."""
    return next(iter(_query(db, root or library_path(), book_id)), None)


def books_by_tag(tag: str, db: Path | None = None) -> list[Book]:
    """Exact tag match, case-insensitive (the tags table is COLLATE NOCASE)."""
    want = tag.strip().casefold()
    return [b for b in load_books(db) if any(t.casefold() == want for t in b.tags)]


def iter_tags(db: Path | None = None, min_count: int = 1) -> list[tuple[str, int]]:
    """The live controlled vocabulary, with usage counts, most-used first."""
    con = connect(db)
    try:
        rows = con.execute(
            """
            SELECT t.name, COUNT(l.book) n
            FROM tags t JOIN books_tags_link l ON l.tag = t.id
            GROUP BY t.name HAVING n >= ? ORDER BY n DESC, t.name
            """,
            (min_count,),
        ).fetchall()
    finally:
        con.close()
    return [(r[0], r[1]) for r in rows]
