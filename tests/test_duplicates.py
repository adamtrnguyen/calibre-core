"""Locks on duplicate detection.

Three signals, because each catches what the others cannot. The one that matters
most is size/hash: it is the ONLY signal that finds the same file catalogued twice
under two different titles, which is exactly what real ids 954 and 995 were —
identical sha256, 49,737,184 bytes, long-form vs short-form title, ~50 MB wasted
and invisible to every title-based check ever run over the library.

`#dupok` suppression is load-bearing in the other direction: a false positive that
cannot be silenced is how a human learns to stop reading the output.
"""

from __future__ import annotations

import hashlib
import sqlite3

from calibre_core.duplicates import (
    _excused,
    dupok_pairs,
    isbn_groups,
    sha256,
    size_groups,
    title_groups,
)


def _set_dupok_raw(library, book_id: int, raw: str) -> None:
    """Write a raw `#dupok` value, so multi-partner strings can be tested."""
    con = sqlite3.connect(library.db)
    cur = con.execute("INSERT INTO custom_column_2 (value) VALUES (?)", (raw,))
    con.execute(
        "INSERT OR IGNORE INTO books_custom_column_2_link (book, value) VALUES (?,?)",
        (book_id, cur.lastrowid),
    )
    con.commit()
    con.close()


# --------------------------------------------------------------------------
# sha256
# --------------------------------------------------------------------------

def test_sha256_matches_hashlib(library):
    p = library.add(1, "A Book", content=b"%PDF-1.4\nexact bytes\n")
    assert sha256(p) == hashlib.sha256(b"%PDF-1.4\nexact bytes\n").hexdigest()


def test_sha256_is_chunk_size_invariant(library):
    """Chunked at 1 MiB so a 300 MB scan does not land in memory. A chunking bug
    would produce a stable but WRONG digest, which reads as 'not a duplicate'."""
    p = library.add(1, "A Book", content=b"x" * 5000)
    assert sha256(p, chunk=7) == sha256(p, chunk=1 << 20)


# --------------------------------------------------------------------------
# dupok_pairs
# --------------------------------------------------------------------------

def test_no_dupok_column_means_no_exemptions(library):
    """A library that never created the column must not error — most won't."""
    assert dupok_pairs() == {}


def test_single_partner_is_parsed(library):
    library.add_dupok_column()
    library.add(2, "Craft of Research 4e")
    library.add(131, "Craft of Research 5e")
    library.pair_dupok(2, 131)
    assert dupok_pairs() == {2: {131}}


def test_multi_partner_values_are_parsed(library):
    """The bug in calibre-mcp's local `_dupok_partners`: it stored the raw column
    value and filtered `if x.isdigit()`, so `"954,995"` failed the digit test as a
    whole string and was silently discarded. Multi-partner exemptions had
    therefore NEVER worked — and nothing reported that they hadn't."""
    library.add_dupok_column()
    library.add(1, "Anchor")
    _set_dupok_raw(library, 1, "954,995")
    assert dupok_pairs() == {1: {954, 995}}


def test_semicolons_work_as_a_separator_too(library):
    """Because a human types the column by hand in the Calibre GUI."""
    library.add_dupok_column()
    library.add(1, "Anchor")
    _set_dupok_raw(library, 1, "954; 995")
    assert dupok_pairs() == {1: {954, 995}}


def test_non_numeric_dupok_entries_are_ignored_not_crashed_on(library):
    """A hand-typed column will contain notes. `"954, see also 995"` should yield
    the ids it can and drop the prose, rather than raising during a scan."""
    library.add_dupok_column()
    library.add(1, "Anchor")
    _set_dupok_raw(library, 1, "954, see also, 995")
    assert dupok_pairs() == {1: {954, 995}}


def test_missing_custom_column_table_is_not_an_error(library):
    """`custom_columns` can name a column whose backing tables were dropped. That
    means 'no exemptions recorded', not a bug — and the except clause is narrowed
    to sqlite3.Error deliberately so it cannot swallow anything else."""
    library.add_dupok_column()
    con = sqlite3.connect(library.db)
    con.execute("DROP TABLE books_custom_column_2_link")
    con.commit()
    con.close()
    assert dupok_pairs() == {}


def test_excused_is_symmetric_and_fails_open(library):
    """Only one side has to name the other. Requiring both would mean a human
    editing two records to silence one pair, and the pair would keep being
    reported until they did — which is how the output stops being read."""
    pairs = {954: {995}}
    assert _excused(954, 995, pairs)
    assert _excused(995, 954, pairs)
    assert not _excused(954, 111, pairs)


# --------------------------------------------------------------------------
# title_groups
# --------------------------------------------------------------------------

def test_groups_two_editions_of_the_same_work(library):
    library.add(2, "The Craft of Research (4th Edition)", authors="Wayne C. Booth")
    library.add(131, "The Craft of Research, Fifth Edition", authors="Wayne C. Booth")
    groups = title_groups()
    assert [[b.id for b in g] for g in groups] == [[2, 131]]


def test_same_title_different_author_is_not_a_duplicate(library):
    """Lang and Artin both wrote *Algebra* (real ids 491, 495). This is why the
    surname is part of the key at all."""
    library.add(491, "Algebra", authors="Serge Lang")
    library.add(495, "Algebra", authors="Michael Artin")
    assert title_groups() == []


def test_series_volumes_stay_apart(library):
    """Stripping everything after ':' collapsed nine Morpho volumes into one
    group — 11 bogus groups over 32 records — because the volume lives in the
    subtitle."""
    for i, part in enumerate(("Simplified Forms", "Hands and Feet", "Muscled Bodies"), 1):
        library.add(i, f"Morpho: {part}", authors="Michel Lauricella")
    assert title_groups() == []


def test_dupok_suppresses_a_deliberate_pair(library):
    """The 4th/5th edition pair is deliberate, so it must stop being reported."""
    library.add_dupok_column()
    library.add(2, "The Craft of Research (4th Edition)", authors="Wayne C. Booth")
    library.add(131, "The Craft of Research, Fifth Edition", authors="Wayne C. Booth")
    library.pair_dupok(2, 131)
    assert title_groups() == []


def test_respect_dupok_false_shows_the_suppressed_pair(library):
    """An audit needs to see what is being hidden from it."""
    library.add_dupok_column()
    library.add(2, "The Craft of Research (4th Edition)", authors="Wayne C. Booth")
    library.add(131, "The Craft of Research, Fifth Edition", authors="Wayne C. Booth")
    library.pair_dupok(2, 131)
    assert [[b.id for b in g] for g in title_groups(respect_dupok=False)] == [[2, 131]]


def test_a_partially_excused_group_is_still_reported(library):
    """Three books, only two of them paired. Suppressing the whole group because
    SOME pair is excused would hide the third — and `writes.py` had exactly that
    bug: it flattened every partner into one global set, so a book named by
    anyone was excused against everyone."""
    library.add_dupok_column()
    for bid in (1, 2, 3):
        library.add(bid, "Same Title", authors="One Author")
    library.pair_dupok(1, 2)
    assert [[b.id for b in g] for g in title_groups()] == [[1, 2, 3]]


def test_untitled_records_are_skipped_rather_than_grouped(library):
    """An empty normalised key must not become a bucket. This is the guard
    omni-rag's audit script lacked: with CJK stripped, four books keyed to '' and
    were dropped from detection entirely — absent, not mis-grouped.

    The two records share an author deliberately. With different authors the
    surname discriminator separates them anyway, so the test would pass with the
    guard removed — it would be asserting nothing. (Caught by mutation: deleting
    `if not k: continue` left the earlier version of this test green.)"""
    library.add(1, "", authors="Same Author")
    library.add(2, "()", authors="Same Author")
    assert title_groups() == []


def test_cjk_titles_group_and_do_not_collapse_together(library):
    """The regression that motivated the package: distinct CJK titles must key
    distinctly, and two copies of one CJK title must still group."""
    library.add(1, "陶哲轩实分析", authors="陶哲轩")
    library.add(2, "线性代数", authors="Georgi E. Shilov")
    library.add(3, "陶哲轩实分析", authors="陶哲轩")
    assert [[b.id for b in g] for g in title_groups()] == [[1, 3]]


# --------------------------------------------------------------------------
# size_groups -- the signal no title check can replace
# --------------------------------------------------------------------------

def test_finds_the_same_file_under_two_different_titles(library):
    """Real ids 954 and 995: byte-identical, catalogued twice, ~50 MB wasted, and
    no title-based check could ever see it."""
    same = b"%PDF-1.4\n" + b"identical payload\n" * 40
    library.add(954, "A Long Descriptive Title With Subtitle", content=same)
    library.add(995, "Short Title", content=same)
    assert [[b.id for b in g] for g in size_groups()] == [[954, 995]]


def test_size_grouping_reads_no_files_at_all(library):
    """The OneDrive property, asserted rather than asserted-in-a-comment. The
    library is a symlink into OneDrive where ~1/3 of files are dataless
    placeholders, and reading ONE byte hydrates the whole file — a 4 KB read of a
    10 MiB placeholder pulled all 10 MiB in 4.5 s. So the size must come from
    `data.uncompressed_size`.

    Here NO file exists on disk at all. If the implementation ever stat()s or
    opens a format, this test fails instead of the real library quietly
    downloading gigabytes."""
    same = b"%PDF-1.4\n" + b"payload\n" * 20
    library.add(954, "One Title", content=same, on_disk=False)
    library.add(995, "Other Title", content=same, on_disk=False)
    assert [[b.id for b in g] for g in size_groups()] == [[954, 995]]


def test_one_book_with_two_same_sized_formats_is_not_a_duplicate(library):
    """A PDF and an EPUB that happen to match in size are one book. Grouping on
    size alone without de-duplicating by id would report it against itself."""
    same = b"%PDF-1.4\nsame length\n"
    library.add(1, "A Book", content=same, fmt="PDF")
    con = sqlite3.connect(library.db)
    con.execute(
        "INSERT INTO data (book, format, uncompressed_size, name) VALUES (1,'EPUB',?,'A Book - X')",
        (len(same),),
    )
    con.commit()
    con.close()
    assert size_groups() == []


def test_zero_byte_formats_do_not_all_group_together(library):
    """A size of 0 means 'unknown' often enough that grouping on it would put
    every unknown-size record in one enormous bogus group."""
    library.add(1, "One", content=b"")
    library.add(2, "Two", content=b"")
    assert size_groups() == []


def test_dupok_suppresses_a_size_pair_too(library):
    """A deliberate born-digital + scan pair matches on neither title nor ISBN but
    can match on size, so the exemption has to apply to this signal as well."""
    library.add_dupok_column()
    same = b"%PDF-1.4\n" + b"payload\n" * 20
    library.add(954, "One Title", content=same)
    library.add(995, "Other Title", content=same)
    library.pair_dupok(954, 995)
    assert size_groups() == []


# --------------------------------------------------------------------------
# isbn_groups
# --------------------------------------------------------------------------

def test_identical_isbns_group(library):
    """Real defect this catches: books 49 and 1057 both carry `4412957810`, an
    ISBN whose group prefix is Japan while both books are British. A WRONG ISBN is
    worse than a missing one — it manufactures a duplicate on the primary key."""
    library.add(49, "Anatomy for Artists", authors="Jahirul Amin", isbn="4412957810")
    library.add(1057, "The Complete Make-Up Artist", authors="Penny Delamar", isbn="4412957810")
    assert [[b.id for b in g] for g in isbn_groups()] == [[49, 1057]]


def test_isbn_punctuation_does_not_prevent_a_match(library):
    """One record hyphenated, one not, is the same edition."""
    library.add(1, "A Book", isbn="978-0-367-86027-1")
    library.add(2, "A Book Reprint", isbn="9780367860271")
    assert [[b.id for b in g] for g in isbn_groups()] == [[1, 2]]


def test_books_without_isbns_are_not_grouped_with_each_other(library):
    """761 records in the real library have no ISBN. Treating absence as a shared
    value would put all of them in one group."""
    library.add(1, "One")
    library.add(2, "Two")
    assert isbn_groups() == []


def test_malformed_isbns_do_not_match_each_other(library):
    """`clean_isbn` goes through isbnlib.canonical, which rejects wrong lengths,
    so garbage cannot match garbage. The hand-rolled `re.sub(r"[^0-9Xx]","")` it
    replaced kept a 12-digit string and would have paired these two."""
    library.add(1, "One", isbn="978111945678")
    library.add(2, "Two", isbn="978111945678")
    assert isbn_groups() == []
