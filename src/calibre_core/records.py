"""The canonical Book record and the queries that build it.

`formats` carries ABSOLUTE PATHS, not format codes. The two shapes existed in
different repos — calibre-mcp's get_book returned `data.format` strings while
omni-rag's audit needed real paths — and reconciling them is the one genuinely
fiddly part of this extraction. Paths win because a path can always be reduced to
its suffix, while a code cannot be turned back into a path without re-querying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from calibre_core.library import connect, library_path

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


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    formats: tuple[Path, ...] = ()
    sizes: tuple[int, ...] = ()
    isbn: str | None = None
    path: str = ""
    uuid: str | None = None
    timestamp: str | None = None
    pubdate: str | None = None
    last_modified: str | None = None
    # Appended rather than filed next to `isbn` where it belongs bibliographically,
    # because every field here is positional as well as keyword: inserting mid-list
    # would silently shift `path`/`uuid`/`timestamp` for any caller that builds a
    # Book positionally, and a wrong-but-plausible uuid breaks deep links quietly.
    publisher: str | None = None
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def authors_str(self) -> str:
        """Ampersand-joined, matching the house convention and the old SQL."""
        return " & ".join(self.authors)

    @property
    def calibre_url(self) -> str | None:
        """A clickable deep link. Needs the uuid — the numeric id will not do."""
        return f"calibre://show-book/_hex_-43616c69627265/{self.uuid}" if self.uuid else None


def _split(s: str | None, sep: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in (s or "").split(sep) if x.strip())


def load_books(db: Path | None = None) -> list[Book]:
    """Every book, with formats resolved to absolute paths.

    One query for books plus one for formats — never a single join across both
    the authors and formats fan-outs, which multiplies rows. (And
    `GROUP_CONCAT(DISTINCT x, sep)` is a syntax error in SQLite: DISTINCT
    aggregates take exactly one argument.)
    """
    lib = library_path()
    con = connect(db)
    try:
        rows = con.execute(_BOOKS_SQL).fetchall()
        fmts: dict[int, list[tuple[Path, int]]] = {}
        # books.path is needed to build each format's absolute path, so collect
        # it from the rows above rather than re-querying per format.
        paths = {r[0]: r[2] for r in rows}
        for bid, name, fmt, size in con.execute(
            "SELECT book, name, format, uncompressed_size FROM data"
        ):
            if bid in paths:
                fmts.setdefault(bid, []).append(
                    (lib / paths[bid] / f"{name}.{str(fmt).lower()}", int(size or 0))
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
                authors=_split(authors, "&"),
                tags=_split(tags, ","),
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


def get_book(book_id: int, db: Path | None = None) -> Book | None:
    return next((b for b in load_books(db) if b.id == book_id), None)


def books_by_tag(tag: str, db: Path | None = None) -> list[Book]:
    """Exact tag match, case-insensitive (the tags table is COLLATE NOCASE)."""
    want = tag.strip().casefold()
    return [b for b in load_books(db) if any(t.casefold() == want for t in b.tags)]


def iter_tags(db: Path | None = None, min_count: int = 1) -> list[tuple[str, int]]:
    """The live controlled vocabulary, with usage counts, most-used first.

    This IS the vocabulary — there is no hand-maintained list to consult. A copied
    list drifts in one direction (the library always grows past it) and then reads
    as authoritative, which is worse than having none.
    """
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
