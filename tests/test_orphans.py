"""Locks on catalogue/filesystem integrity, in both directions.

The check this replaced was one-directional — a directory with no DB row — so it
could not see a row whose file had vanished, nor case-only path drift. It also
walked `.caltrash`, which in Adam's library is 99 files across 33 directories:
every deleted book reported as an orphan, and 839 MiB hydrated from OneDrive on
every single run.
"""

from __future__ import annotations

import sqlite3

from calibre_core.orphans import missing_formats, orphan_dirs, path_case_drift

# --------------------------------------------------------------------------
# orphan_dirs -- a directory the catalogue does not know about
# --------------------------------------------------------------------------

def test_finds_a_directory_with_no_database_row(library):
    """The state a metadata.db rollback leaves: the catalogue reverts, the
    filesystem does not. Invisible to the GUI and to `calibredb list`, but
    omni-rag ingests it while the UUID resolver returns None — so the book loses
    its calibre:// deep links with no error anywhere."""
    library.add(1, "Real Book")
    library.orphan_dir(77, "Ghost Book")
    found = orphan_dirs()
    assert [o["id"] for o in found] == [77]
    assert found[0]["files"] == ["ghost.pdf"]


def test_caltrash_is_excluded(library):
    """THE expensive one. `.caltrash` holds deleted books, so walking it both
    reports them as orphans and hydrates discarded files from OneDrive — 839 MiB
    per run in the real library. calibre-mcp's local `find_orphans` still lacks
    this exclusion."""
    library.add(1, "Real Book")
    trash = library.root / ".caltrash" / "Deleted Book (404)"
    trash.mkdir(parents=True)
    (trash / "deleted.pdf").write_bytes(b"%PDF-1.4\ndeleted\n")
    assert orphan_dirs() == []


def test_calnotes_is_excluded(library):
    library.add(1, "Real Book")
    notes = library.root / ".calnotes" / "Some Note (405)"
    notes.mkdir(parents=True)
    (notes / "note.pdf").write_bytes(b"%PDF-1.4\nnote\n")
    assert orphan_dirs() == []


def test_a_directory_whose_id_exists_is_not_an_orphan(library):
    """The ordinary case. If this ever fails, every book in the library is
    reported as an orphan and the report becomes unreadable."""
    library.add(1, "Real Book")
    library.add(2, "Another Real Book")
    assert orphan_dirs() == []


def test_a_directory_without_an_id_suffix_is_ignored(library):
    """Calibre's layout is `Author/Title (id)`. A directory with no `(id)` was
    not created by Calibre, so it is not a stranded book — reporting it would
    mean flagging every stray folder a human ever dropped in the tree."""
    library.add(1, "Real Book")
    stray = library.root / "Some Author" / "Notes And Scans"
    stray.mkdir(parents=True)
    (stray / "loose.pdf").write_bytes(b"%PDF-1.4\nloose\n")
    assert orphan_dirs() == []


def test_only_book_formats_are_listed_for_an_orphan(library):
    """Calibre litters `cover.jpg` and `metadata.opf` into every book directory.
    Listing those as recoverable content would overstate what is there."""
    d = library.root / "Ghost Author" / "Mixed Bag (88)"
    d.mkdir(parents=True)
    (d / "book.epub").write_bytes(b"epub")
    (d / "cover.jpg").write_bytes(b"jpeg")
    (d / "metadata.opf").write_bytes(b"<opf/>")
    found = orphan_dirs()
    assert found[0]["files"] == ["book.epub"]


def test_orphan_path_is_relative_to_the_library(library):
    """Reported relative so the output is readable and machine-independent —
    and this is why `library_path()` must stay unresolved: resolving the OneDrive
    symlink would make `relative_to` produce a physical path instead."""
    library.orphan_dir(99, "Ghost")
    assert orphan_dirs()[0]["path"] == "Ghost Author/Ghost (99)"


def test_comic_and_text_formats_count_as_books(library):
    """`.cbz`/`.cbr`/`.txt` were absent from the original suffix list, so a
    stranded comic archive was invisible to the scan."""
    d = library.root / "Ghost Author" / "A Comic (91)"
    d.mkdir(parents=True)
    (d / "scan.cbz").write_bytes(b"cbz")
    assert orphan_dirs()[0]["files"] == ["scan.cbz"]


# --------------------------------------------------------------------------
# missing_formats -- the reverse: a row whose file is gone
# --------------------------------------------------------------------------

def test_finds_a_row_whose_file_is_absent(library):
    """The direction the old one-way check could not see at all. A record that
    looks fine in the GUI and opens to nothing."""
    library.add(1, "Present Book")
    library.add(2, "Ghost Format", on_disk=False)
    missing = missing_formats()
    assert [m["id"] for m in missing] == [2]
    assert missing[0]["title"] == "Ghost Format"
    assert missing[0]["missing"].endswith(".pdf")


def test_no_missing_formats_when_every_file_is_present(library):
    library.add(1, "One")
    library.add(2, "Two")
    assert missing_formats() == []


# --------------------------------------------------------------------------
# path_case_drift -- the one that hides until an rsync
# --------------------------------------------------------------------------

def test_detects_a_case_only_path_difference(library):
    """On case-insensitive APFS `JOHN MONTAGUE/` satisfies a lookup for
    `John Montague/`: the GUI shows the book and `Path.exists()` returns True.
    The link breaks only when the library is rsynced to a case-sensitive target
    (the NAS, HPC scratch, any Linux box), which is far too late to notice.

    So this compares against `iterdir()`, not `exists()`."""
    library.add(1, "A Book", authors="John Montague", path="John Montague/A Book (1)")
    # Rename the on-disk directory to differ only in case, as OneDrive/rsync does.
    (library.root / "John Montague").rename(library.root / "JOHN MONTAGUE")

    drift = path_case_drift()
    assert len(drift) == 1
    assert drift[0]["expected"] == "John Montague"
    assert drift[0]["on_disk"] == "JOHN MONTAGUE"
    assert drift[0]["id"] == 1


def test_case_drift_is_detected_even_where_exists_lies(library):
    """Pins the mechanism rather than the symptom. On a case-insensitive
    filesystem the naive check returns True — if a future refactor swaps
    `iterdir()` for `exists()`, this test is what fails."""
    library.add(1, "A Book", authors="John Montague", path="John Montague/A Book (1)")
    (library.root / "John Montague").rename(library.root / "JOHN MONTAGUE")

    naive_says_fine = (library.root / "John Montague").exists()
    if naive_says_fine:  # case-insensitive fs, e.g. default APFS
        assert path_case_drift(), "exists() lied and the scan believed it"
    else:  # case-sensitive fs
        assert path_case_drift(), "case-sensitive fs and the scan still must report it"


def test_no_drift_reported_when_the_path_matches_exactly(library):
    """Guards against the inverse failure: a scan that flags every book."""
    library.add(1, "A Book", authors="John Montague")
    library.add(2, "Another", authors="Jane Doe")
    assert path_case_drift() == []


def test_a_wholly_missing_directory_is_not_reported_as_case_drift(library):
    """A vanished directory is `missing_formats`' business. Reporting it here too
    would double-count it and imply a rename that never happened."""
    library.add(1, "A Book", authors="John Montague")
    con = sqlite3.connect(library.db)
    con.execute("UPDATE books SET path = 'Nobody At All/A Book (1)' WHERE id = 1")
    con.commit()
    con.close()
    assert path_case_drift() == []
