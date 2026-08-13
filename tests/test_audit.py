"""Tests for the metadata audit.

The audit is a report, so most of these pin the CLASSIFICATION rules — the ones
that decide whether a record lands in a human's cleanup queue. A rule that
silently stops firing turns the queue empty and looks like success.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from calibre_core import audit
from calibre_core.records import Book, load_books


def _book(bid: int = 1, **kw) -> Book:
    defaults = {"id": bid, "title": "A Book", "authors": ("An Author",)}
    return Book(**{**defaults, **kw})


# --------------------------------------------------------------------------
# the regression that made --scan-missing-isbns unusable
# --------------------------------------------------------------------------

def test_progress_reads_book_attributes_not_subscripts(capsys, monkeypatch):
    """`Book` is a frozen dataclass with no `__getitem__`.

    The version this moved from printed `id={book['id']}` here, and this line
    fires on `completed_count == 1` — so the FIRST scanned PDF raised TypeError
    and took the whole run down at any `--progress-every` above 0 (default 25).
    Nothing else in the file used a subscript, which is why it survived review.

    Asserted with a real `Book`, deliberately: a dict or tuple stand-in supports
    subscripting and would let the bug back in.
    """
    monkeypatch.setattr(
        audit, "scan_pdf_for_isbns", lambda p, pages, timeout: {"path": str(p), "isbns": []}
    )
    out = audit.run_pdf_scans(
        [(_book(7, title="Scanned"), Path("/tmp/x.pdf"))],
        pages_each_end=2,
        scan_timeout=5,
        scan_workers=1,
        progress_every=1,
    )
    assert out == [{"id": 7, "title": "Scanned", "authors": "An Author", "path": "/tmp/x.pdf", "isbns": []}]
    assert "id=7 Scanned" in capsys.readouterr().err


def test_progress_can_be_silenced(capsys, monkeypatch):
    monkeypatch.setattr(
        audit, "scan_pdf_for_isbns", lambda p, pages, timeout: {"path": str(p), "isbns": []}
    )
    audit.run_pdf_scans(
        [(_book(7), Path("/tmp/x.pdf"))],
        pages_each_end=2, scan_timeout=5, scan_workers=1, progress_every=0,
    )
    assert capsys.readouterr().err == ""


def test_scan_results_come_back_in_the_order_given(monkeypatch):
    """`as_completed` yields in FINISH order. A report built off that reshuffles
    every run and diffs against itself, so the order is restored."""
    monkeypatch.setattr(
        audit, "scan_pdf_for_isbns", lambda p, pages, timeout: {"path": str(p), "isbns": []}
    )
    jobs = [(_book(b), Path(f"/tmp/{b}.pdf")) for b in (30, 10, 20)]
    out = audit.run_pdf_scans(jobs, pages_each_end=2, scan_timeout=5, scan_workers=3, progress_every=0)
    assert [r["id"] for r in out] == [30, 10, 20]


def test_a_scan_worker_exception_is_data_not_a_crash(monkeypatch):
    """One hostile PDF must cost one record, not the run."""
    def boom(p, pages, timeout):
        raise OSError("device not configured")

    monkeypatch.setattr(audit, "scan_pdf_for_isbns", boom)
    out = audit.run_pdf_scans(
        [(_book(5), Path("/tmp/bad.pdf"))],
        pages_each_end=2, scan_timeout=5, scan_workers=1, progress_every=0,
    )
    assert out[0]["error"] == "OSError: device not configured"


def test_the_scan_subprocess_reenters_by_module_not_by_file():
    """`-m calibre_core.audit`, not `__file__`. As a loose script this re-invoked
    its own path, which stops working the moment the code is installed as a
    package rather than sitting on disk as the file that started the process.

    Checked against the AST so a comment mentioning `__file__` cannot pass it.
    """
    tree = ast.parse(Path(audit.__file__).read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "scan_pdf_for_isbns"
    )
    literals = [
        n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert "-m" in literals and "calibre_core.audit" in literals
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "__file__" not in names
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "__file__" not in attrs


# --------------------------------------------------------------------------
# classification rules
# --------------------------------------------------------------------------

def test_calibres_zero_year_counts_as_a_missing_pubdate():
    """Calibre stores "no date" as year 0101, not NULL. An emptiness test alone
    reports every undated book as dated, which empties the queue this feeds."""
    assert audit.missing_pubdate(_book(pubdate="0101-01-01T00:00:00+00:00")) is True
    assert audit.missing_pubdate(_book(pubdate="")) is True
    assert audit.missing_pubdate(_book(pubdate="1998-04-01T00:00:00+00:00")) is False


@pytest.mark.parametrize("authors,bad", [
    (("Unknown",), True),
    (("unknown author",), True),
    (("Administrator",), True),
    (("Administrateur",), True),   # Calibre's French default
    (("calibre",), True),
    ((), True),                    # authorless -> authors_str is ""
    (("Donald E. Knuth",), False),
])
def test_placeholder_authors_are_flagged(authors, bad):
    assert audit.bad_author(_book(authors=authors)) is bad


@pytest.mark.parametrize("title,junk", [
    ("Microsoft Word - chapter3.doc", True),   # a Word export, not a title
    ("Something.pdf", True),                   # the filename leaked into the field
    ("Book.epub", True),
    ("B00KX7T4M2 Some Book", True),            # an Amazon ASIN
    ("ebok 2003", True),
    ("", True),
    ("   ", True),
    ("Statistical Rethinking", False),
    ("The C Programming Language", False),
])
def test_filename_shaped_titles_are_flagged(title, junk):
    assert audit.junk_title(_book(title=title)) is junk


def test_a_valid_isbn_is_kept_and_an_invalid_one_is_not():
    """`record_isbn` returns "" for a failed checksum, so a typo'd identifier is
    reported as missing rather than looked up — a lookup on a wrong-but-plausible
    ISBN returns a real edition of the WRONG book."""
    assert audit.record_isbn(_book(isbn="9780262035613")) == "9780262035613"
    assert audit.record_isbn(_book(isbn="9780262035610")) == ""   # last digit wrong
    assert audit.record_isbn(_book(isbn=None)) == ""


# --------------------------------------------------------------------------
# ISBN extraction from page text
# --------------------------------------------------------------------------

def test_a_labelled_isbn_is_found_including_the_isbn_10_form():
    """The regex leaves the label IN the match on purpose — `clean_isbn` strips
    it. An earlier version re-scanned each match for a "bare" number, which ran
    the label's own `10` in `ISBN-10 0262035618` into the digit run and produced a
    12-digit string that failed the checksum. It LOST ISBNs rather than being
    merely redundant."""
    assert audit.extract_isbns_from_text("ISBN-10 0262035618") == ["0262035618"]
    assert audit.extract_isbns_from_text("ISBN: 978-0-262-03561-3") == ["9780262035613"]
    assert audit.extract_isbns_from_text("ISBN-13: 9780262035613") == ["9780262035613"]


def test_isbn_13_sorts_first():
    """A copyright page lists both forms of the same book; the 13 is the one worth
    looking up, so it must be the one `[0]` picks."""
    text = "ISBN-10 0262035618 and ISBN-13 978-0-262-03561-3"
    assert audit.extract_isbns_from_text(text)[0] == "9780262035613"


def test_a_failed_checksum_is_not_reported_as_an_isbn():
    """Otherwise page numbers and phone numbers become "found ISBNs"."""
    assert audit.extract_isbns_from_text("ISBN 1234567890123") == []


def test_no_isbn_in_the_text_is_an_empty_list():
    assert audit.extract_isbns_from_text("Copyright 2019. All rights reserved.") == []


def test_a_missing_pymupdf_is_reported_per_file_not_raised(monkeypatch, tmp_path):
    """The scan is an optional extra. Without it the audit still runs and each
    scanned file carries the reason, which is what makes the extra discoverable."""
    monkeypatch.setitem(__import__("sys").modules, "pymupdf", None)
    out = audit._scan_pdf_text_layer(tmp_path / "x.pdf", 4)
    assert "pdf' extra" in out["error"]


# --------------------------------------------------------------------------
# the report, over a real library
# --------------------------------------------------------------------------

def test_audit_over_a_library_classifies_every_record(library):
    library.add(1, "Good Book", authors="Real Author", tags="math", isbn="9780262035613",
                publisher="MIT Press")
    library.add(2, "Microsoft Word - draft.doc", authors="Unknown")
    report = audit.audit(library.root)

    assert report["summary"]["books"] == 2
    by_id = {r["id"]: r for r in report["records"]}
    assert by_id[1]["isbn"] == "9780262035613"
    assert "missing-isbn" not in by_id[1]["issues"]
    assert {"missing-isbn", "bad-author", "junk-title", "missing-tags"} <= set(by_id[2]["issues"])


def test_duplicate_groups_come_from_the_library_under_audit(library, library_at, tmp_path):
    """`title_groups()` with no `db=` falls back to $CALIBRE_LIBRARY. If the audit
    passed `--library` to `load_books` but not to `title_groups`, the report would
    silently mix two catalogues: records from the one asked for, duplicate groups
    from the configured one.

    Built so the two libraries DISAGREE — the configured one has a duplicate pair
    and the audited one does not — so a fallback shows up as a non-empty group
    list.
    """
    library.add(1, "Craft", authors="Ann Author")
    library.add(2, "Craft", authors="Ann Author")   # a real duplicate, in $CALIBRE_LIBRARY

    other = library_at(tmp_path / "Elsewhere")
    other.add(1, "Only One Book", authors="Bee Bee")

    report = audit.audit(other.root)
    assert report["summary"]["books"] == 1
    assert report["duplicate_title_groups"] == [], (
        "duplicate groups leaked in from $CALIBRE_LIBRARY — `db=` was not threaded through"
    )


def test_the_fill_queue_needs_both_an_isbn_and_a_gap_to_fill(library):
    """The highest-value output: an ISBN to look up AND somewhere to put the
    answer. A complete record is not work, and a gap with no ISBN is not
    actionable — so the queue is the intersection, not either half.

    (The fixture defaults `pubdate` to CURRENT_TIMESTAMP, as real Calibre does, so
    book 2 here is genuinely complete rather than merely untested.)
    """
    library.add(1, "Has ISBN No Publisher", isbn="9780262035613")
    library.add(2, "Complete Record", isbn="9780131103627", publisher="Prentice Hall")
    library.add(3, "No ISBN No Publisher")
    report = audit.audit(library.root)
    assert [r["id"] for r in report["safe_existing_isbn_lookup"]] == [1]


def test_pdf_extraction_candidates_need_a_pdf(library):
    """An EPUB-only record with no ISBN cannot be helped by a text-layer scan."""
    library.add(1, "No ISBN PDF", fmt="PDF")
    library.add(2, "No ISBN EPUB", fmt="EPUB")
    report = audit.audit(library.root)
    assert [r["id"] for r in report["pdf_isbn_extraction_candidates"]] == [1]


def test_scanning_is_off_unless_asked_for(library):
    """It shells out per book and reads files off OneDrive — never the default."""
    library.add(1, "No ISBN", fmt="PDF")
    report = audit.audit(library.root)
    assert report["isbn_scan"] == []
    assert report["summary"]["scanned_pdf_candidates"] == 0


def test_the_scan_limit_caps_the_work(library, monkeypatch):
    for i in range(1, 6):
        library.add(i, f"Book {i}", fmt="PDF")
    monkeypatch.setattr(
        audit, "scan_pdf_for_isbns", lambda p, pages, timeout: {"path": str(p), "isbns": []}
    )
    report = audit.audit(library.root, scan_missing=True, limit_scan=2, progress_every=0)
    assert report["summary"]["scanned_pdf_candidates"] == 2


def test_the_report_is_json_serialisable(library):
    """It is written to disk as JSON; a Path or a set in there fails at the end of
    a long run, after the expensive part."""
    library.add(1, "A Book", isbn="9780262035613")
    json.dumps(audit.audit(library.root))


def test_write_markdown_renders_and_reports_what_it_elided(library, tmp_path):
    library.add(1, "Microsoft Word - x.doc", authors="Unknown")
    report = audit.audit(library.root)
    out = tmp_path / "r.md"
    audit.write_markdown(report, out)
    text = out.read_text()
    assert "# Calibre Metadata Audit" in text
    assert "junk-title" in text
    assert "## Duplicate Title Groups" in text


def test_load_books_and_audit_see_the_same_library(library):
    """Guards the seam the previous test exercises from the other side."""
    library.add(1, "A Book")
    assert len(load_books(db=library.db)) == len(audit.audit(library.root)["records"])
