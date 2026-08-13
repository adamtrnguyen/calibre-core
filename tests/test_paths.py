"""Locks on path -> record, the primitive three consumers each wrote themselves.

The interesting cases are all about what is NOT a book in this library. The
callers hand this function paths they have no reason to believe are Calibre books
— every document open in Skim, every file under an ingest root — so "no" has to
be an ordinary answer rather than an exception.
"""

from __future__ import annotations

from calibre_core.paths import book_id_from_dir, library_root_for, resolve_path

# --------------------------------------------------------------------------
# the id folder -- the whole of Calibre's path->record mapping
# --------------------------------------------------------------------------

def test_book_id_comes_from_the_folder_not_the_filename():
    """Calibre truncates the title in the FILENAME at ~41 chars, which merges books
    sharing a prefix (both Rockhe Kim "Line Drawing Technique" volumes collapsed
    onto one string in omni-rag). The folder carries the id, so that is what is
    read."""
    assert book_id_from_dir("Statistical Rethinking (501)") == 501


def test_a_folder_with_no_trailing_id_is_not_a_book_folder():
    """Anchored at the END. An unanchored search would read "Volume (2) Of Two" as
    book 2, and Calibre never names a folder that way."""
    assert book_id_from_dir("Notes And Scans") is None
    assert book_id_from_dir("Volume (2) Of Two") is None


def test_resolves_a_file_inside_the_library(library):
    staged = library.add(501, "Statistical Rethinking", authors="Richard McElreath")
    book = resolve_path(staged)
    assert book is not None
    assert (book.id, book.uuid, book.title) == (501, "uuid-501", "Statistical Rethinking")


def test_resolution_is_by_id_folder_so_a_renamed_file_still_resolves(library):
    """The filename is not an identifier — Calibre rewrites it on every metadata
    edit. Only the folder's `(id)` is load-bearing."""
    staged = library.add(7, "A Book")
    assert resolve_path(staged.with_name("something-else-entirely.pdf")).id == 7


def test_the_record_is_whole_not_just_an_id(library):
    """The three implementations this replaces each returned a different fragment
    (a uuid, a display string, a uuid+title+authors tuple), so each consumer
    re-queried for whatever it also needed. Returning `Book` is what stops the
    next consumer from writing query number four."""
    staged = library.add(9, "Full Record", authors="A Writer & B Writer", tags="reference")
    book = resolve_path(staged)
    assert book.authors_str == "A Writer & B Writer"
    assert book.tags == ("reference",)
    assert book.formats == (staged,)
    assert book.calibre_url.endswith("uuid-9")


# --------------------------------------------------------------------------
# the symlink case -- a resolved path against an unresolved library
# --------------------------------------------------------------------------

def test_a_resolved_path_matches_an_unresolved_library(tmp_path, library_at, monkeypatch):
    """The real shape on this Mac: `~/Calibre Library` is a symlink into OneDrive,
    `library_path()` returns it UNRESOLVED by contract, and Skim reports the
    physical path of an open document. Comparing the two as-given makes every real
    lookup miss — this is the test that fails if either `.resolve()` is dropped."""
    physical = tmp_path / "OneDrive" / "Calibre Library"
    builder = library_at(physical)
    link = tmp_path / "Calibre Library"
    link.symlink_to(physical)
    monkeypatch.setenv("CALIBRE_LIBRARY", str(link))

    physical_file = builder.add(3, "Symlinked Book")  # what Skim would report
    through_link = link / physical_file.relative_to(physical)  # what the library says
    assert through_link != physical_file  # the premise: the two forms really differ
    assert through_link.resolve() == physical_file

    assert resolve_path(physical_file).id == 3  # the Skim case
    assert resolve_path(through_link).id == 3  # and the unresolved form still works


def test_the_returned_formats_stay_under_the_unresolved_library(
    tmp_path, library_at, monkeypatch
):
    """Only the COMPARISON resolves. The record's format paths keep the library's
    unresolved form, because that is what `orphans`' `relative_to` and every
    calibre:// deep link are built against."""
    physical = tmp_path / "OneDrive" / "Calibre Library"
    builder = library_at(physical)
    link = tmp_path / "Calibre Library"
    link.symlink_to(physical)
    monkeypatch.setenv("CALIBRE_LIBRARY", str(link))

    book = resolve_path(builder.add(4, "Symlinked Book").resolve())
    assert str(book.formats[0]).startswith(str(link))


# --------------------------------------------------------------------------
# every flavour of "not a book in this library" -> None, never an exception
# --------------------------------------------------------------------------

def test_a_path_outside_the_library_is_none_not_an_exception(library, tmp_path):
    """calibre-page-inserter skips the foreign PDFs open alongside library books in
    Skim, and omni-rag's resolver is a documented no-op off a non-Calibre tree.
    Both need an answer, not a traceback."""
    library.add(1, "A Book")
    foreign = tmp_path / "Downloads" / "Some Paper (99)" / "paper.pdf"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"%PDF-1.4\nnot ours\n")
    assert resolve_path(foreign) is None


def test_an_id_folder_outside_the_library_does_not_resolve_by_coincidence(library, tmp_path):
    """The nastiest version of the previous case: a foreign folder that happens to
    end in `(1)` resolves to book 1 on the id alone if containment is not checked,
    and the plugin inserts a deep link to a book the reader is not reading."""
    library.add(1, "The Real Book One")
    lookalike = tmp_path / "elsewhere" / "Unrelated (1)" / "file.pdf"
    lookalike.parent.mkdir(parents=True)
    lookalike.write_bytes(b"%PDF-1.4\nimpostor\n")
    assert resolve_path(lookalike) is None


def test_a_sibling_directory_sharing_the_librarys_name_prefix_does_not_resolve(
    library, tmp_path
):
    """Containment is a path-ancestor test, not a string prefix. `<tmp>/Calibre
    Library Backup/…` starts with `<tmp>/Calibre Library`, so a `startswith`
    implementation resolves books out of a backup, an export, or a second library
    that merely sorts next to the real one."""
    library.add(1, "A Book")
    backup = tmp_path / f"{library.root.name} Backup" / "An Author" / "A Book (1)" / "book.pdf"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"%PDF-1.4\nbackup\n")
    assert resolve_path(backup) is None


def test_a_file_with_no_id_folder_is_none(library):
    stray = library.root / "Some Author" / "Notes And Scans" / "loose.pdf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"%PDF-1.4\nloose\n")
    assert resolve_path(stray) is None


def test_an_orphan_directory_resolves_to_none(library):
    """A book folder whose row is gone — what a metadata.db rollback leaves.
    `orphans.orphan_dirs` is what reports those; here it is just unresolvable."""
    d = library.orphan_dir(77, "Ghost Book")
    assert resolve_path(d / "ghost.pdf") is None


# --------------------------------------------------------------------------
# discover_root -- omni-rag's case, which library_path() cannot serve
# --------------------------------------------------------------------------

def test_library_root_for_finds_the_nearest_metadata_db(library):
    assert library_root_for(library.add(1, "A Book")) == library.root


def test_library_root_for_is_none_off_a_non_calibre_tree(tmp_path):
    f = tmp_path / "not-a-library" / "file.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert library_root_for(f) is None


def test_discover_root_resolves_a_library_that_is_not_the_configured_one(
    tmp_path, library_at, guard_real_library
):
    """omni-rag's ingest runs against a staged copy of the library on HPC scratch,
    which carries its own metadata.db at the root and is not the configured library
    on the machine doing the ingesting. `CALIBRE_LIBRARY` points at nothing here, so
    a pass means the root really came from the walk up."""
    export = library_at(tmp_path / "scratch" / "books")
    staged = export.add(12, "Exported Book")
    assert resolve_path(staged) is None  # not the configured library
    assert resolve_path(staged, discover_root=True).id == 12


def test_discover_root_hangs_format_paths_off_the_discovered_root(
    tmp_path, library_at, guard_real_library
):
    """The trap the `root` argument exists for. With format paths built from
    `library_path()` instead, every path on the returned record would point into a
    tree the catalogue knows nothing about — and `missing_formats` would report the
    whole export as missing."""
    export = library_at(tmp_path / "scratch" / "books")
    staged = export.add(12, "Exported Book")
    book = resolve_path(staged, discover_root=True)
    assert book.formats[0] == staged
    assert book.formats[0].exists()


def test_discover_root_is_none_off_a_non_calibre_tree(tmp_path, guard_real_library):
    """The graceful no-op omni-rag's docstring promises: pointed at a folder of
    loose PDFs, ingest resolves no uuid and carries on."""
    f = tmp_path / "Downloads" / "Paper (5)" / "paper.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"%PDF-1.4\n")
    assert resolve_path(f, discover_root=True) is None
