"""Tests for writing an outline into a library PDF.

These use REAL PDFs and a real `os.replace`. The thing under test is not "does it
call set_toc" — it is "can a bad save ever become the original", and only an
actual save-verify-replace cycle can answer that.
"""

from __future__ import annotations

import pytest

from calibre_core.toc import has_outline, inject_outline, sanitize_outline
from calibre_core.writes import WriteBlocked

pymupdf = pytest.importorskip("pymupdf")


def _pdf(path, pages: int = 6, outline: list | None = None, text: str = "page"):
    """A small readable PDF. Each page carries distinct text so a verification
    check comparing sampled page text can actually detect a change."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} {i}")
    if outline:
        doc.set_toc(outline)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture()
def gui_closed(monkeypatch):
    """Every test here must assert against the write path, not the gate. The one
    test that DOES exercise the gate overrides this."""
    monkeypatch.setattr("calibre_core.toc.gui_is_open", lambda: False)


# --------------------------------------------------------------------------
# sanitize_outline — the three rules pymupdf raises on
# --------------------------------------------------------------------------

def test_first_entry_is_forced_to_level_one():
    """A printed TOC often starts at a sub-heading, and `set_toc` raises if the
    first entry is not level 1 — so the whole book would fail over one entry."""
    out = sanitize_outline([{"level": 3, "title": "Sub", "pdf_page": 2}], 10)
    assert out == [[1, "Sub", 2]]


def test_a_level_may_only_step_up_by_one():
    """1 -> 3 raises in pymupdf. It happens whenever a middle heading was missed
    during extraction, which is common, so it is clamped rather than rejected."""
    out = sanitize_outline(
        [
            {"level": 1, "title": "Part", "pdf_page": 1},
            {"level": 3, "title": "Deep", "pdf_page": 2},
        ],
        10,
    )
    assert [e[0] for e in out] == [1, 2]


def test_levels_may_step_down_freely():
    """Only the upward step is constrained. Clamping downward steps too would
    flatten a real hierarchy back to one level."""
    out = sanitize_outline(
        [
            {"level": 1, "title": "A", "pdf_page": 1},
            {"level": 2, "title": "A.1", "pdf_page": 2},
            {"level": 3, "title": "A.1.a", "pdf_page": 3},
            {"level": 1, "title": "B", "pdf_page": 4},
        ],
        10,
    )
    assert [e[0] for e in out] == [1, 2, 3, 1]


def test_pages_are_clamped_into_range():
    """A page target computed from a printed folio with the wrong offset lands
    past the end; `set_toc` raises on out-of-range."""
    out = sanitize_outline(
        [{"level": 1, "title": "A", "pdf_page": 0}, {"level": 1, "title": "B", "pdf_page": 9999}],
        10,
    )
    assert [e[2] for e in out] == [1, 10]


def test_untitled_and_unpaged_entries_are_dropped_not_repaired():
    """A bookmark with no text is unclickable, and a guessed page is worse than a
    missing one — so neither is invented."""
    out = sanitize_outline(
        [
            {"level": 1, "title": "   ", "pdf_page": 1},
            {"level": 1, "title": "No page", "pdf_page": None},
            {"level": 1, "title": "Kept", "pdf_page": 2},
        ],
        10,
    )
    assert out == [[1, "Kept", 2]]


def test_titles_have_their_whitespace_collapsed():
    """Extracted titles carry the source's line wrapping."""
    out = sanitize_outline([{"level": 1, "title": "A  long\n title", "pdf_page": 1}], 10)
    assert out[0][1] == "A long title"


# --------------------------------------------------------------------------
# has_outline — the all-empty-titles case
# --------------------------------------------------------------------------

def test_an_outline_of_empty_titles_does_not_count_as_an_outline(tmp_path):
    """Structurally present, useless to a reader, invisible to an extractor —
    which is how such a file lands in an injection work list. Counting it would
    make those books permanently unfixable."""
    p = _pdf(tmp_path / "blank_titles.pdf", outline=[[1, " ", 1], [1, "", 2]])
    assert has_outline(p) is False


def test_a_real_outline_counts(tmp_path):
    p = _pdf(tmp_path / "has.pdf", outline=[[1, "Chapter 1", 1]])
    assert has_outline(p) is True


# --------------------------------------------------------------------------
# the gate — a batch precondition, so it RAISES
# --------------------------------------------------------------------------

def test_an_open_gui_raises_rather_than_returning_a_reason(tmp_path, monkeypatch):
    """This is the reason the function moved into calibre-core: as
    `scripts/inject_toc.py:_inject` it had no gate at all, so a batch run during
    an open Calibre swapped files out from under the viewer.

    It raises rather than returning `ok: False` because it is equally true for
    every remaining file — a caller looping over 700 books wants to stop, not to
    silently write nothing 700 times.
    """
    monkeypatch.setattr("calibre_core.toc.gui_is_open", lambda: True)
    p = _pdf(tmp_path / "book.pdf")
    with pytest.raises(WriteBlocked, match="GUI is open"):
        inject_outline(p, [{"level": 1, "title": "C1", "pdf_page": 1}], tmp_path / "bak")


def test_the_gate_fires_before_the_file_is_even_opened(tmp_path, monkeypatch):
    """Ordering matters: a missing file must not mask an open GUI, or a batch over
    a stale work list reports 'file missing' and never mentions the real problem."""
    monkeypatch.setattr("calibre_core.toc.gui_is_open", lambda: True)
    with pytest.raises(WriteBlocked, match="GUI is open"):
        inject_outline(tmp_path / "nope.pdf", [{"level": 1, "title": "C", "pdf_page": 1}], tmp_path)


# --------------------------------------------------------------------------
# per-file outcomes — returned, so a batch keeps going
# --------------------------------------------------------------------------

def test_a_missing_file_is_a_returned_reason(tmp_path, gui_closed):
    out = inject_outline(
        tmp_path / "gone.pdf", [{"level": 1, "title": "C", "pdf_page": 1}], tmp_path
    )
    assert out == {"ok": False, "reason": "file missing"}


def test_no_injectable_entries_is_a_returned_reason(tmp_path, gui_closed):
    p = _pdf(tmp_path / "book.pdf")
    out = inject_outline(p, [{"level": 1, "title": "", "pdf_page": None}], tmp_path / "bak")
    assert out["ok"] is False
    assert "no injectable entries" in out["reason"]


def test_an_existing_outline_is_skipped_so_reruns_are_idempotent(tmp_path, gui_closed):
    p = _pdf(tmp_path / "book.pdf", outline=[[1, "Already", 1]])
    out = inject_outline(p, [{"level": 1, "title": "New", "pdf_page": 2}], tmp_path / "bak")
    assert out["ok"] is False
    assert "already has an outline" in out["reason"]
    # and the existing outline is untouched
    with pymupdf.open(str(p)) as doc:
        assert [t[1] for t in doc.get_toc(simple=True)] == ["Already"]


def test_replace_existing_overrides_the_idempotence_guard(tmp_path, gui_closed):
    p = _pdf(tmp_path / "book.pdf", outline=[[1, "Old", 1]])
    out = inject_outline(
        p, [{"level": 1, "title": "New", "pdf_page": 2}], tmp_path / "bak", replace_existing=True
    )
    assert out["ok"] is True
    with pymupdf.open(str(p)) as doc:
        assert [t[1] for t in doc.get_toc(simple=True)] == ["New"]


# --------------------------------------------------------------------------
# the success path, and what it guarantees
# --------------------------------------------------------------------------

def test_a_successful_injection_writes_the_outline_and_keeps_a_backup(tmp_path, gui_closed):
    p = _pdf(tmp_path / "book.pdf", pages=8)
    backups = tmp_path / "bak"
    out = inject_outline(
        p,
        [
            {"level": 1, "title": "Chapter 1", "pdf_page": 1},
            {"level": 2, "title": "Section 1.1", "pdf_page": 3},
        ],
        backups,
    )
    assert out["ok"] is True and out["entries"] == 2

    with pymupdf.open(str(p)) as doc:
        assert [(t[0], t[1], t[2]) for t in doc.get_toc(simple=True)] == [
            (1, "Chapter 1", 1),
            (2, "Section 1.1", 3),
        ]

    backup = backups / "book.pdf"
    assert backup.exists()
    # The backup is the PRE-injection file, which is the only thing that makes it
    # a rollback: a copy taken after the replace would carry the new outline.
    with pymupdf.open(str(backup)) as old:
        assert old.get_toc(simple=True) == []


def test_the_page_text_survives_injection(tmp_path, gui_closed):
    """`set_toc` rewrites document structure. If it ever reflowed or dropped
    content the book would be quietly damaged, so the content is asserted, not
    just the outline."""
    p = _pdf(tmp_path / "book.pdf", pages=5, text="unique-marker")
    inject_outline(p, [{"level": 1, "title": "C", "pdf_page": 1}], tmp_path / "bak")
    with pymupdf.open(str(p)) as doc:
        assert doc.page_count == 5
        assert "unique-marker 0" in doc[0].get_text()
        assert "unique-marker 4" in doc[4].get_text()


def test_no_temp_file_is_left_behind_on_success(tmp_path, gui_closed):
    p = _pdf(tmp_path / "book.pdf")
    inject_outline(p, [{"level": 1, "title": "C", "pdf_page": 1}], tmp_path / "bak")
    assert list(tmp_path.glob("*.tocinject.pdf")) == []


# --------------------------------------------------------------------------
# the failure this whole design exists for
# --------------------------------------------------------------------------

def test_a_failed_verification_leaves_the_original_untouched(tmp_path, gui_closed, monkeypatch):
    """The defended failure is a save that SUCCEEDS and damages the file. Forcing
    the verification to fail must leave the original byte-identical and remove the
    temp copy — no backup is even needed, because nothing moved."""
    p = _pdf(tmp_path / "book.pdf", pages=4, text="original")
    before = p.read_bytes()

    real_open = pymupdf.open

    def open_with_broken_check(target, *a, **kw):
        # Only the verification reopen (of the .tocinject.pdf temp) is sabotaged.
        # Note there is no `monkeypatch.undo()` here: `monkeypatch` is
        # function-scoped, so it is the SAME object the `gui_closed` fixture used,
        # and undoing it mid-test would quietly restore the real GUI check.
        if str(target).endswith(".tocinject.pdf"):
            raise RuntimeError("simulated unreadable save")
        return real_open(target, *a, **kw)

    monkeypatch.setattr("pymupdf.open", open_with_broken_check)
    out = inject_outline(p, [{"level": 1, "title": "C", "pdf_page": 1}], tmp_path / "bak")

    assert out["ok"] is False
    assert "unreadable" in out["reason"]
    assert p.read_bytes() == before, "the original was modified despite a failed save"
    assert list(tmp_path.glob("*.tocinject.pdf")) == [], "temp copy left behind"


def test_a_verification_mismatch_is_reported_and_cleaned_up(tmp_path, gui_closed, monkeypatch):
    """Same defence, via the other branch: the copy reopens fine but does not
    match. `set_toc` is stubbed to write FEWER entries than requested, which is
    exactly the shape of a silently-lossy save."""
    p = _pdf(tmp_path / "book.pdf", pages=4)
    before = p.read_bytes()

    # Bound BEFORE patching — reaching for `pymupdf.Document.set_toc` inside the
    # replacement finds the replacement and recurses.
    real_set_toc = pymupdf.Document.set_toc

    def lossy_set_toc(self, toc, *a, **kw):
        return real_set_toc(self, toc[:1], *a, **kw)

    monkeypatch.setattr(pymupdf.Document, "set_toc", lossy_set_toc)
    out = inject_outline(
        p,
        [
            {"level": 1, "title": "A", "pdf_page": 1},
            {"level": 1, "title": "B", "pdf_page": 2},
        ],
        tmp_path / "bak",
    )
    assert out["ok"] is False
    assert "verification failed" in out["reason"]
    assert p.read_bytes() == before
    assert list(tmp_path.glob("*.tocinject.pdf")) == []
