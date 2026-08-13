"""Fixture that builds a throwaway Calibre library on disk.

Two things make this necessary rather than convenient:

  * The package defaults to Adam's REAL library (`~/Calibre Library`), so a test
    that forgets to redirect would read — or with the write path, mutate — live
    data.
  * `calibredb` cannot be used to create the fixture. Against a hand-made
    library it rewrites the file into the full 48-table Calibre schema and drops
    every row, so the schema is transcribed by hand in fixtures/schema.sql.

Redirection works through `CALIBRE_LIBRARY`, which `matching.library_path()`
reads at CALL time rather than import time. That is the only injection seam the
package actually honours: `load_books(db=...)` accepts a path but no caller
passes one, so it does nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

SCHEMA = Path(__file__).parent / "fixtures" / "schema.sql"


class LibraryBuilder:
    """Builds `Author/Title (id)/Name.fmt` on disk plus matching DB rows.

    Both halves matter: `find_orphans` walks the directory tree, while
    everything else reads the DB, so a fixture with only one half silently
    exercises half the code.
    """

    def __init__(self, root: Path):
        self.root = root
        self.db = root / "metadata.db"
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA.read_text())
        con.commit()
        con.close()
        self._next_author = 1
        self._next_tag = 1
        self._next_publisher = 1

    def add(
        self,
        book_id: int,
        title: str,
        authors: str = "Test Author",
        tags: str = "",
        fmt: str = "PDF",
        content: bytes = b"%PDF-1.4\nfixture\n",
        isbn: str | None = None,
        publisher: str | None = None,
        on_disk: bool = True,
        path: str | None = None,
    ) -> Path:
        """Insert one book. Returns the format file's path.

        on_disk=False inserts the DB row but writes no file — the A1 case
        (a record whose format is missing).
        """
        con = sqlite3.connect(self.db)
        safe_title = title.replace(":", "_").replace("/", "_")
        # Calibre files an authorless book under "Unknown"; without this the
        # relative path starts with "/" and mkdir targets the filesystem root.
        first_author = authors.split("&")[0].strip() or "Unknown"
        rel = path if path is not None else f"{first_author}/{safe_title} ({book_id})"
        con.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, uuid) VALUES (?,?,?,?,?,?)",
            (book_id, title, title, first_author, rel, f"uuid-{book_id}"),
        )
        for name in [a.strip() for a in authors.split("&") if a.strip()]:
            row = con.execute("SELECT id FROM authors WHERE name = ?", (name,)).fetchone()
            if row:
                aid = row[0]
            else:
                aid = self._next_author
                self._next_author += 1
                con.execute("INSERT INTO authors (id, name, sort) VALUES (?,?,?)", (aid, name, name))
            con.execute(
                "INSERT OR IGNORE INTO books_authors_link (book, author) VALUES (?,?)",
                (book_id, aid),
            )
        for tname in [t.strip() for t in tags.split(",") if t.strip()]:
            row = con.execute("SELECT id FROM tags WHERE name = ?", (tname,)).fetchone()
            if row:
                tid = row[0]
            else:
                tid = self._next_tag
                self._next_tag += 1
                con.execute("INSERT INTO tags (id, name) VALUES (?,?)", (tid, tname))
            con.execute(
                "INSERT OR IGNORE INTO books_tags_link (book, tag) VALUES (?,?)", (book_id, tid)
            )
        if isbn:
            con.execute(
                "INSERT INTO identifiers (book, type, val) VALUES (?,'isbn',?)", (book_id, isbn)
            )
        if publisher:
            # Interned in `publishers` and joined through the link table, exactly
            # as Calibre stores it — NOT a column on `books`, which has none. A
            # fixture that faked a `books.publisher` column would let a query
            # against a column that does not exist pass here and fail on the real
            # library.
            row = con.execute(
                "SELECT id FROM publishers WHERE name = ?", (publisher,)
            ).fetchone()
            if row:
                pid = row[0]
            else:
                pid = self._next_publisher
                self._next_publisher += 1
                con.execute(
                    "INSERT INTO publishers (id, name, sort) VALUES (?,?,?)",
                    (pid, publisher, publisher),
                )
            con.execute(
                "INSERT OR IGNORE INTO books_publishers_link (book, publisher) VALUES (?,?)",
                (book_id, pid),
            )

        fname = f"{safe_title[:40]} - {first_author}"
        target = self.root / rel / f"{fname}.{fmt.lower()}"
        if on_disk:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # uncompressed_size mirrors the real column, which the duplicate check
        # reads instead of stat()ing files — that is what keeps it cheap.
        con.execute(
            "INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
            (book_id, fmt.upper(), len(content), fname),
        )
        con.commit()
        con.close()
        return target

    def add_dupok_column(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO custom_columns (id,label,name,datatype,is_multiple,normalized) "
            "VALUES (2,'dupok','Dup OK','text',1,1)"
        )
        con.commit()
        con.close()

    def pair_dupok(self, book_id: int, partner_id: int) -> None:
        """Mark book_id as deliberately duplicating partner_id."""
        con = sqlite3.connect(self.db)
        val = str(partner_id)
        row = con.execute("SELECT id FROM custom_column_2 WHERE value = ?", (val,)).fetchone()
        if row:
            vid = row[0]
        else:
            cur = con.execute("INSERT INTO custom_column_2 (value) VALUES (?)", (val,))
            vid = cur.lastrowid
        con.execute(
            "INSERT OR IGNORE INTO books_custom_column_2_link (book, value) VALUES (?,?)",
            (book_id, vid),
        )
        con.commit()
        con.close()

    def orphan_dir(self, book_id: int, title: str = "Orphaned Book") -> Path:
        """A directory with a book file but no DB row — the A2 case."""
        d = self.root / "Ghost Author" / f"{title} ({book_id})"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ghost.pdf").write_bytes(b"%PDF-1.4\norphan\n")
        return d


@pytest.fixture()
def library(tmp_path, monkeypatch):
    """An empty throwaway library, with CALIBRE_LIBRARY pointed at it."""
    root = tmp_path / "Calibre Library"
    builder = LibraryBuilder(root)
    monkeypatch.setenv("CALIBRE_LIBRARY", str(root))
    return builder


@pytest.fixture()
def guard_real_library(monkeypatch):
    """Make the real library unreachable, so a test that forgets to redirect fails loudly."""
    monkeypatch.setenv("CALIBRE_LIBRARY", "/nonexistent/definitely-not-a-library")
