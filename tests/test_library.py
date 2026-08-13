"""Locks on the read-only chokepoint.

`connect()` exists so that read-only-ness is a property of the package rather
than of whoever writes the next call site. Before it, `?mode=ro` was copy-pasted
at five sites in calibre-mcp and two in omni-rag's UUID resolver, and one script
opened metadata.db with a bare read-WRITE connection to run a SELECT. These tests
assert the guarantee holds, because "we always remember the flag" is not one.
"""

from __future__ import annotations

import sqlite3

import pytest

from calibre_core.library import (
    DEFAULT_LIBRARY,
    LibraryNotFound,
    connect,
    custom_column_id,
    db_path,
    library_path,
    schema_probe,
)

# --------------------------------------------------------------------------
# the guarantee
# --------------------------------------------------------------------------

def test_connect_refuses_writes(library):
    """The whole reason this function exists. A connection that happens to be
    used only for SELECTs today is not read-only; this one cannot write."""
    con = connect()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO books (id, title) VALUES (999, 'Smuggled In')")
    con.close()


def test_connect_refuses_writes_to_link_tables_too(library):
    """Not just `books` — the link tables are where a careless UPDATE would
    desynchronise Calibre's derived state."""
    library.add(1, "A Book")
    con = connect()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DELETE FROM books_authors_link WHERE book = 1")
    con.close()


def test_connect_raises_library_not_found_when_db_absent(tmp_path, monkeypatch):
    """A typo'd CALIBRE_LIBRARY must fail loudly, not create an empty database.
    `sqlite3.connect` on a missing path would silently make one; `?mode=ro`
    would raise an opaque OperationalError. Neither says what is wrong."""
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "not-a-library"))
    with pytest.raises(LibraryNotFound, match="no metadata.db"):
        connect()


# --------------------------------------------------------------------------
# the injection seam
# --------------------------------------------------------------------------

def test_library_path_reads_the_environment_at_call_time(tmp_path, monkeypatch):
    """Read at import time this would be un-redirectable, every test would hit
    the real library, and the write path would mutate live data."""
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "one"))
    assert library_path() == tmp_path / "one"
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "two"))
    assert library_path() == tmp_path / "two"


def test_library_path_is_returned_unresolved(tmp_path, monkeypatch):
    """Deliberate contract: the real library is a symlink into OneDrive, and
    resolving here would change `Path.relative_to` output in orphan scanning.
    calibre-page-inserter calls `.resolve()` itself for exactly this reason."""
    physical = tmp_path / "physical"
    physical.mkdir()
    link = tmp_path / "Calibre Library"
    link.symlink_to(physical)
    monkeypatch.setenv("CALIBRE_LIBRARY", str(link))
    assert library_path() == link
    assert library_path() != physical
    assert library_path().resolve() == physical.resolve()


def test_default_library_is_the_real_one(guard_real_library):
    """Documents the default the fixtures exist to redirect away from."""
    assert DEFAULT_LIBRARY.name == "Calibre Library"


def test_db_path_hangs_off_library_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "lib"))
    assert db_path() == tmp_path / "lib" / "metadata.db"


def test_explicit_db_argument_overrides_the_environment(library, tmp_path, monkeypatch):
    """`connect(db=...)` must win, so a caller can point at a backup copy."""
    library.add(1, "In The Fixture")
    real = library.db
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "nonexistent"))
    con = connect(real)
    try:
        assert con.execute("SELECT title FROM books WHERE id=1").fetchone()[0] == "In The Fixture"
    finally:
        con.close()


# --------------------------------------------------------------------------
# schema_probe -- one place a Calibre upgrade can break loudly
# --------------------------------------------------------------------------

def test_schema_probe_is_empty_on_a_good_schema(library):
    """If this fails, fixtures/schema.sql has drifted from what the code reads
    and every other test in the suite is testing the wrong shape."""
    assert schema_probe() == {}


def test_schema_probe_names_a_missing_column(library):
    """A renamed column is the realistic Calibre-upgrade failure. Without this
    probe, seven call sites quietly return wrong answers instead of one raising."""
    con = sqlite3.connect(library.db)
    con.execute("ALTER TABLE data RENAME COLUMN uncompressed_size TO size_bytes")
    con.commit()
    con.close()
    missing = schema_probe()
    assert missing == {"data": ["uncompressed_size"]}


def test_schema_probe_names_a_missing_table(library):
    con = sqlite3.connect(library.db)
    con.execute("DROP TABLE identifiers")
    con.commit()
    con.close()
    assert schema_probe()["identifiers"] == ["<table absent>"]


def test_schema_probe_covers_the_publisher_tables(library):
    """`load_books` joins these as of 0.2.0, and a table it reads but does not
    probe is the failure mode `REQUIRED_SCHEMA` exists to prevent: without the
    entry, a Calibre schema lacking them turns every `load_books` call into a raw
    `no such table` OperationalError from inside a subquery, instead of one probe
    naming what is gone."""
    con = sqlite3.connect(library.db)
    con.execute("DROP TABLE books_publishers_link")
    con.commit()
    con.close()
    assert schema_probe()["books_publishers_link"] == ["<table absent>"]


def test_schema_probe_raises_rather_than_reporting_everything_missing(tmp_path, monkeypatch):
    """No database is a different failure from a wrong schema, and conflating
    them would report 7 missing tables for a path typo."""
    monkeypatch.setenv("CALIBRE_LIBRARY", str(tmp_path / "gone"))
    with pytest.raises(LibraryNotFound):
        schema_probe()


# --------------------------------------------------------------------------
# custom columns
# --------------------------------------------------------------------------

def test_custom_column_id_is_discovered_not_assumed(library):
    """`#dupok` materialises as `custom_column_<id>` + `books_custom_column_<id>_link`,
    and the id differs per library — it is 2 here and 2 in Adam's, but hardcoding
    it would silently query the wrong column on any other machine."""
    assert custom_column_id("dupok") is None
    library.add_dupok_column()
    assert custom_column_id("dupok") == 2


def test_custom_column_id_returns_none_for_an_absent_label(library):
    """None, not an exception: "this library has no such column" is a normal
    answer, and `dupok_pairs` depends on being able to ask cheaply."""
    library.add_dupok_column()
    assert custom_column_id("no_such_column") is None
