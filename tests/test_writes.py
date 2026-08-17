"""Tests for the gated write path.

These exist because `writes.py` is now the only sanctioned route for adding a
book or changing metadata — `calibredb add` and `calibredb set_metadata` are
denied in Claude Code's permission settings. A silent regression here would
reopen exactly the defects the gate was built to stop:

  * a byte-identical file catalogued twice under two titles (found in the real
    library: two records, same sha256, ~50 MB wasted, invisible to any
    title-based check)
  * a record with title and author swapped, from `calibredb add` parsing a
    filename as "Title - Author" when the source named it "Author - Title"
  * a rename that moves a directory with no possible rollback

Every test runs against a throwaway library. None touches the real one.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

import pytest

from calibre_core.writes import (
    WriteBlocked,
    _merge_identifiers,
    add_book,
    check_duplicate,
    dedup_key,
    reject_html_entities,
    remove_identifier,
    set_book_metadata,
)

# --------------------------------------------------------------------------
# dedup_key — the normaliser. Its bugs are silent and expensive.
# --------------------------------------------------------------------------

def test_editions_collapse_to_one_key():
    """Two editions of one work must group, so the pair surfaces for review."""
    assert dedup_key("The Craft of Research (4th Edition)") == dedup_key(
        "The Craft of Research, Fifth Edition"
    )


def test_subtitle_is_kept_so_series_volumes_stay_distinct():
    """Regression: stripping after ':' collapsed 9 Morpho volumes into one group.

    That produced 11 bogus duplicate groups over 32 records in the real library.
    """
    keys = {
        dedup_key("Morpho: Simplified Forms"),
        dedup_key("Morpho: Hands and Feet"),
        dedup_key("Morpho: Muscled Bodies"),
    }
    assert len(keys) == 3


def test_ordinal_only_stripped_before_the_word_edition():
    """'Second Edition' loses its ordinal; 'The Second Tutorial' keeps it."""
    assert "second" not in dedup_key("Some Book, Second Edition")
    assert "second" in dedup_key("The Second Tutorial")


def test_cjk_titles_survive_normalisation():
    """Regression: a `[^a-z0-9]` strip mapped every CJK title to '', so those
    books vanished from duplicate detection entirely."""
    a, b = dedup_key("陶哲轩实分析"), dedup_key("线性代数")
    assert a and b and a != b


def test_hangul_survives_because_nfkd_decomposes_it_into_jamo():
    """The test above passed while Korean was still broken, because it only
    asserted Chinese. Keeping 가-힯 in the allowed class is NOT sufficient: NFKD
    decomposes 한 into conjoining Jamo (U+1112 U+1161 U+11AB) in the U+1100
    block, which that class does not cover.

    Synthetic on purpose: the library's only Korean title (393) carries it in a
    parenthetical that dedup_key strips anyway, so that record never collapsed.
    The defect was still live in this gate -- it just had no real witness."""
    assert dedup_key("한국어 해부학")
    assert dedup_key("매일 스케치 인물") != dedup_key("한국어 해부학")


def test_hangul_survives_from_decomposed_input():
    """macOS stores filenames NFD, so a title can arrive already decomposed."""
    assert dedup_key(unicodedata.normalize("NFD", "한국어")) == dedup_key("한국어")


def test_kana_voicing_is_not_stripped_as_a_diacritic():
    """NFKD splits the dakuten off as a combining mark and the diacritic strip
    deleted it: ドキドキ became トキトキ and が became か (books 922, 923). Voicing
    is phonemic, so that is a different word and distinct titles can collide."""
    assert "ドキドキ" in dedup_key("ちょっとドキドキする女の子")
    assert dedup_key("が") != dedup_key("か")


def test_accents_normalise_so_hebert_matches_hebert():
    assert dedup_key("Hébert Optics") == dedup_key("Hebert Optics")


def test_halfwidth_kana_folds_onto_fullwidth():
    """Non-CJK still goes through NFKD, which is what makes a halfwidth title
    reachable from a fullwidth query."""
    assert dedup_key("ｷｬﾗｸﾀｰ") == dedup_key("キャラクター")


# --------------------------------------------------------------------------
# check_duplicate — verdict tiers
# --------------------------------------------------------------------------

def test_identical_file_blocks(library):
    """sha256 match is a hard stop: no judgement is required."""
    staged = library.add(1, "Applied Photographic Optics: Lenses", authors="Sidney F. Ray")
    r = check_duplicate(staged_path=str(staged), title="Applied Photographic Optics",
                        authors="Sidney F. Ray")
    assert r["verdict"] == "block"
    assert any(s["signal"] == "sha256" for s in r["signals"])


def test_same_isbn_blocks(library):
    library.add(1, "Some Book", authors="A Writer", isbn="9781119744825")
    r = check_duplicate(title="Totally Different Title", authors="Other Person",
                        isbn="978-1-119-74482-5")
    assert r["verdict"] == "block"
    assert any(s["signal"] == "isbn" for s in r["signals"])


def test_isbn_matching_ignores_punctuation(library):
    """Hyphenation must not defeat the check — the same ISBN is written both ways."""
    library.add(1, "Some Book", authors="A Writer", isbn="978-1-119-74482-5")
    r = check_duplicate(title="X", authors="Y", isbn="9781119744825")
    assert r["verdict"] == "block"


def test_same_title_and_author_only_warns(library):
    """A title+author match needs a human: it could be two editions."""
    library.add(1, "Systems Thinking, Systems Practice", authors="Peter Checkland")
    r = check_duplicate(title="Systems Thinking, Systems Practice", authors="Peter Checkland")
    assert r["verdict"] == "warn"


def test_dupok_suppresses_the_warning(library):
    """A pair explicitly marked deliberate must stop nagging on every run."""
    library.add_dupok_column()
    library.add(2, "The Craft of Research (4th Edition)", authors="Wayne C. Booth")
    library.add(131, "The Craft of Research, Fifth Edition", authors="Wayne C. Booth")
    library.pair_dupok(2, 131)
    library.pair_dupok(131, 2)
    r = check_duplicate(title="The Craft of Research, Fifth Edition", authors="Wayne C. Booth")
    assert r["verdict"] == "ok"


def test_unrelated_book_is_ok(library):
    library.add(1, "Something Else Entirely", authors="Nobody Relevant")
    r = check_duplicate(title="A Book That Does Not Exist Here", authors="Unknown Person")
    assert r["verdict"] == "ok"
    assert r["signals"] == []


def test_different_author_same_title_does_not_block(library):
    """Lang vs Artin 'Algebra' are different books, not duplicates."""
    library.add(1, "Algebra", authors="Serge Lang")
    r = check_duplicate(title="Algebra", authors="Michael Artin")
    assert r["verdict"] == "ok"


# --------------------------------------------------------------------------
# set_book_metadata — refusals that prevent unrecoverable damage
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["title", "authors", "Title", "AUTHORS"])
def test_rename_fields_are_refused_by_default(library, field):
    """Neither is writable without asking for it, and the check is case-insensitive.

    They are refused for DIFFERENT reasons and only `title` has a way through — see
    the three tests at the bottom of this file. The earlier version of this test
    asserted one shared reason ("rollback"), which is what hid that they are not
    equivalent."""
    library.add(1, "A Book", authors="An Author")
    with pytest.raises(WriteBlocked):
        set_book_metadata(1, {field: "Something New"})


def test_protected_field_refused_even_alongside_a_safe_one(library):
    """A mixed write must be refused whole, not partially applied."""
    library.add(1, "A Book", authors="An Author")
    with pytest.raises(WriteBlocked):
        set_book_metadata(1, {"tags": "safe-tag", "title": "Sneaky Rename"})


# --------------------------------------------------------------------------
# identifier merging
#
# `calibredb set_metadata --field identifiers:...` REPLACES the whole set. Doing
# it plainly on real book 256 deleted its `zotero` identifier -- the link to the
# Zotero facade item, so its citations and zotero:// deep links -- while setting
# an ISBN. Nothing in calibredb's output announced the loss.
# --------------------------------------------------------------------------

def test_setting_one_identifier_preserves_the_others(library):
    """The regression that cost book 256 its Zotero link."""
    library.add(1, "A Book", authors="An Author", isbn="9780367860271")
    con = sqlite3.connect(library.db)
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (1,'zotero','TSV8YEFG')")
    con.commit()
    con.close()

    merged = _merge_identifiers(1, "isbn:9781909414310")
    assert "zotero:TSV8YEFG" in merged
    assert "isbn:9781909414310" in merged
    assert "isbn:9780367860271" not in merged  # same type overwrites


def test_merging_adds_a_new_type_without_disturbing_existing(library):
    library.add(2, "Another Book", authors="Some Author", isbn="9780367860271")
    merged = _merge_identifiers(2, "doi:10.1000/xyz")
    assert "isbn:9780367860271" in merged
    assert "doi:10.1000/xyz" in merged


def test_merge_ignores_malformed_pairs(library):
    library.add(3, "Third", authors="Third Author", isbn="9780367860271")
    merged = _merge_identifiers(3, "garbage-no-colon, ,isbn:9781909414310")
    assert merged == "isbn:9781909414310"


# --------------------------------------------------------------------------
# add_book preflight
#
# The add path itself cannot be tested — `calibredb add` rewrites a fixture
# library to 48 tables and drops every row. Only the checks that raise BEFORE
# calibredb is reached are exercisable here.
# --------------------------------------------------------------------------

def test_extensionless_staged_file_is_refused(tmp_path):
    """Given no extension calibredb exits 0, prints nothing, and adds nothing.
    Caught for real: a download saved as `/tmp/dl-<md5>` returned ok=True with
    an empty book_ids list, which reads as success."""
    staged = tmp_path / "dl-0618f00fbb4e4cb4654e5a833d5be2a8"
    staged.write_bytes(b"%PDF-1.4\nno extension\n")
    with pytest.raises(WriteBlocked, match="no extension"):
        add_book(str(staged), title="A Book", authors="An Author")


def test_missing_staged_file_is_refused(tmp_path):
    with pytest.raises(WriteBlocked, match="does not exist"):
        add_book(str(tmp_path / "absent.pdf"), title="A Book", authors="An Author")


def test_add_still_demands_title_and_authors(tmp_path):
    """The extension check must not preempt the inverted-record guard."""
    staged = tmp_path / "book.pdf"
    staged.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(WriteBlocked, match="mandatory"):
        add_book(str(staged), title="", authors="")


# --------------------------------------------------------------------------
# the fixture itself — if this drifts, every test above is meaningless
# --------------------------------------------------------------------------

def test_fixture_writes_both_db_row_and_file(library):
    """Half a fixture silently exercises half the code: find_orphans walks the
    tree while everything else reads the DB."""
    staged = library.add(7, "Both Halves", authors="Some Author")
    assert staged.exists()
    con = sqlite3.connect(f"file:{library.db}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM books WHERE id=7").fetchone()[0] == 1
    assert con.execute("SELECT uncompressed_size FROM data WHERE book=7").fetchone()[0] == len(
        b"%PDF-1.4\nfixture\n"
    )
    con.close()


def test_missing_file_case_is_representable(library):
    """on_disk=False gives the A1 violation: a row whose format file is absent."""
    staged = library.add(8, "Ghost Format", on_disk=False)
    assert not staged.exists()
    con = sqlite3.connect(f"file:{library.db}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM data WHERE book=8").fetchone()[0] == 1
    con.close()


def test_remove_identifier_refuses_a_no_op(library):
    """A removal that removes nothing must fail loudly, not report success — the
    caller has misidentified the record or the type, and a cheerful ok=True there
    would hide it."""
    library.add(1, "A Book", authors="An Author", isbn="9780367860271")
    with pytest.raises(WriteBlocked, match="no 'doi' identifier"):
        remove_identifier(1, "doi")


def test_remove_identifier_is_a_separate_function_from_the_merging_setter(library):
    """Removal is deliberately NOT expressible through set_book_metadata, which
    merges. Book 256 lost its zotero link because a plain identifier write
    replaced the whole set; the fix made that path merge, which in turn made
    removal unreachable there — on purpose. This asserts the two are distinct
    entry points so a later refactor cannot quietly fold them together."""
    import calibre_core.writes as w

    assert w.remove_identifier is not w.set_book_metadata
    assert "id_type" in w.remove_identifier.__code__.co_varnames


def test_dupok_does_not_excuse_a_pair_against_the_whole_library(library):
    """The flattened-allowlist bug, demonstrated.

    The old code collected every #dupok partner id into ONE set and suppressed a
    title+author signal on mere membership. So if an unrelated record named books
    1 and 2 as ITS partners, both became excused against everything -- and a
    genuine unexplained duplicate between 1 and 2 reported `ok`.

    Suppression must require the two matched records to be paired with EACH
    OTHER, not merely to appear somewhere in the column."""
    library.add_dupok_column()
    library.add(1, "Shared Title", authors="One Author")
    library.add(2, "Shared Title", authors="One Author")
    library.add(99, "Something Unrelated Entirely", authors="Other Person")
    # 99 names both 1 and 2. Under the old flattening this excused them.
    library.pair_dupok(99, 1)
    library.pair_dupok(99, 2)

    r = check_duplicate(title="Shared Title", authors="One Author")
    assert r["verdict"] == "warn", "a real duplicate was excused by an unrelated pairing"
    assert {s["book_id"] for s in r["signals"] if s["signal"] == "title+author"} == {1, 2}


# --------------------------------------------------------------------------
# title vs authors — the blanket refusal was wrong, and they are not equivalent
# --------------------------------------------------------------------------

def test_authors_is_refused_even_with_force(library):
    """A relocation, not a rename: the book moves to a DIFFERENT author directory,
    leaving the old one possibly empty and the record somewhere no `(id)` walk from
    the previous location reaches. No force flag opens this."""
    library.add(1, "A Book", authors="Old Name")
    for force in (False, True):
        with pytest.raises(WriteBlocked, match="different author directory"):
            set_book_metadata(1, {"authors": "New Name"}, force=force)


def test_title_is_refused_by_default_but_names_the_way_through(library):
    """Refused unless asked for deliberately — but the message has to say HOW,
    or the caller concludes it is impossible and goes around the gate."""
    library.add(1, "A Book")
    with pytest.raises(WriteBlocked, match="force=True") as e:
        set_book_metadata(1, {"title": "Better Title"})
    assert e.value.detail["hint"] == "force=True"


def test_the_old_blanket_refusal_is_gone(library, monkeypatch):
    """Regression lock on the CHANGE. `title` and `authors` were refused together
    with the reason "a rename moves the directory and no rollback exists" — which
    does not survive scrutiny, because `add_book` orphans a directory on rollback
    too and is allowed. Only `authors` deserved an absolute rule.

    Stops at the gate: `_run` is stubbed, so this asserts what the gate permits,
    not what calibredb does with it.
    """
    library.add(1, "A Book")
    monkeypatch.setattr("calibre_core.writes._run", lambda args: "ok")
    out = set_book_metadata(1, {"title": "Better Title"}, force=True)
    assert out["ok"] is True


def test_a_title_write_still_backs_up_first(library, monkeypatch):
    """force=True relaxes WHICH field, never the preconditions around it."""
    library.add(1, "A Book")
    monkeypatch.setattr("calibre_core.writes._run", lambda args: "ok")
    out = set_book_metadata(1, {"title": "Better Title"}, force=True)
    assert Path(out["db_backup"]).exists()


def test_a_title_write_still_refuses_while_the_gui_is_open(library, monkeypatch):
    """The GUI gate is not a field-level rule and force must not reach it —
    calibredb corrupts state if it writes while Calibre is running."""
    library.add(1, "A Book")
    monkeypatch.setattr("calibre_core.writes.gui_is_open", lambda: True)
    with pytest.raises(WriteBlocked, match="GUI is open"):
        set_book_metadata(1, {"title": "Better Title"}, force=True)


def test_other_fields_never_needed_force(library, monkeypatch):
    """Guard against the flag leaking into a general requirement — comments, tags
    and pubdate rename nothing and must stay reachable without it."""
    library.add(1, "A Book")
    monkeypatch.setattr("calibre_core.writes._run", lambda args: "ok")
    assert set_book_metadata(1, {"comments": "a note", "tags": "x"})["ok"] is True


# --- HTML entities -----------------------------------------------------------
# `&` is Calibre's AUTHOR SEPARATOR. An escaped "&amp;" therefore does not merely
# render wrong: calibredb splits on the bare `&` and files a phantom author.
# Regression for a real incident -- add_book(authors="Rudolf von Laban &amp; Lisa
# Ullmann") returned success and stored ['Rudolf von Laban', 'amp; Lisa Ullmann'].
# It had to be removed and re-added, because set_book_metadata refuses `authors`
# outright, so the gate permitted a write it gave no way to repair.


def test_the_incident_value_is_refused():
    with pytest.raises(WriteBlocked) as ei:
        reject_html_entities(authors="Rudolf von Laban &amp; Lisa Ullmann")
    assert ei.value.detail["field"] == "authors"
    assert ei.value.detail["entity"] == "&amp;"


@pytest.mark.parametrize(
    "value",
    [
        "A &amp; B", "A &lt; B", "A &gt; B", 'say &quot;hi&quot;',
        "it&#39;s", "it&apos;s", "a&nbsp;b",
    ],
)
def test_every_covered_entity_is_refused(value):
    with pytest.raises(WriteBlocked):
        reject_html_entities(title=value)


def test_literal_ampersand_is_the_correct_form_and_passes():
    # This is what the caller SHOULD send, and it must not be mangled or refused:
    # calibredb splits it into two real authors, which is the intent.
    reject_html_entities(authors="Rudolf von Laban & Lisa Ullmann",
                         title="Cause & Effect", tags="art,gesture")


def test_non_string_values_pass_through():
    # a `fields` dict legitimately carries ints and None
    reject_html_entities(book_id=1234, pubdate=None, rating=5)


def test_bare_amp_word_is_not_a_false_positive():
    # "amp" alone, or "&" followed by a word, is not an entity
    reject_html_entities(title="Amplifier Design", authors="Amp Jones & B Smith")


def test_add_book_refuses_entities_before_touching_the_library(library, tmp_path):
    """The guard must fire BEFORE any duplicate scan, backup or calibredb call."""
    staged = tmp_path / "book.pdf"
    staged.write_bytes(b"%PDF-1.4 staged")
    with pytest.raises(WriteBlocked) as ei:
        add_book(path=str(staged), title="T", authors="A &amp; B")
    assert ei.value.detail["entity"] == "&amp;"


# --- add_format ---------------------------------------------------------------
# For a record whose only format no parser reads (a .cbz/.cbr comic, a .mobi) and which
# has been converted. The gate that matters: `calibredb add_format` REPLACES a same-type
# format silently, with no undo -- so a second PDF over an existing PDF destroys it.


def test_add_format_refuses_replacing_an_existing_format(library, tmp_path):
    """The real hazard. A CBR-only record gaining a PDF is fine; a PDF over a PDF is loss."""
    import calibre_core.writes as w

    library.add(1, "Some Comic", fmt="PDF")
    staged = tmp_path / "again.pdf"
    staged.write_bytes(b"%PDF-1.4 second")
    with pytest.raises(WriteBlocked) as ei:
        w.add_format(book_id=1, path=str(staged), backup_dir=str(tmp_path / "bk"))
    assert ei.value.detail["format"] == "PDF"
    assert ei.value.detail["hint"] == "force=True"
    assert "PDF" in ei.value.detail["existing"]


def test_add_format_allows_a_NEW_format_type(library, tmp_path, monkeypatch):
    """A CBR-only record gaining a PDF is the whole use case — it must not be refused
    by the replace guard, which keys on the format TYPE, not on the record."""
    import calibre_core.writes as w

    library.add(1, "Some Comic", fmt="CBR", content=b"Rar!fixture")
    staged = tmp_path / "converted.pdf"
    staged.write_bytes(b"%PDF-1.4 converted")
    # stop before shelling out: this asserts the GATES pass, not that calibredb works
    monkeypatch.setattr(w, "gui_is_open", lambda: True)
    with pytest.raises(WriteBlocked) as ei:
        w.add_format(book_id=1, path=str(staged), backup_dir=str(tmp_path / "bk"))
    assert "GUI is open" in str(ei.value)  # reached the LAST gate, so the replace guard passed


def test_add_format_refuses_unknown_book_id(library, tmp_path):
    import calibre_core.writes as w

    library.add(1, "Some Book")
    staged = tmp_path / "x.pdf"
    staged.write_bytes(b"%PDF-1.4 x")
    with pytest.raises(WriteBlocked) as ei:
        w.add_format(book_id=999999, path=str(staged), backup_dir=str(tmp_path / "bk"))
    assert "999999" in str(ei.value)


def test_add_format_refuses_missing_and_extensionless(library, tmp_path):
    import calibre_core.writes as w

    library.add(1, "Some Book")
    with pytest.raises(WriteBlocked):
        w.add_format(book_id=1, path=str(tmp_path / "nope.pdf"))
    noext = tmp_path / "noextension"
    noext.write_bytes(b"data")
    with pytest.raises(WriteBlocked):
        w.add_format(book_id=1, path=str(noext))
