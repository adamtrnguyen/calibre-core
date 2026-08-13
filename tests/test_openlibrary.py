"""Tests for the Open Library candidate builder.

No network. `OpenLibraryClient` is replaced by a stub that returns canned edition
JSON, because what is under test is the MATCHING — whether a fetched edition is
judged to be the book we hold — not whether urllib works.
"""

from __future__ import annotations

import pytest

from calibre_core import openlibrary as ol


class FakeClient:
    """Returns whatever edition JSON the test supplies, keyed by ISBN."""

    def __init__(self, editions: dict[str, dict]):
        self.editions = editions
        self.asked: list[str] = []

    def edition(self, isbn: str) -> dict:
        self.asked.append(isbn)
        return {"isbn": isbn, **self.editions.get(isbn, {"error": "http 404"})}


def _edition(title="A Book", authors=("An Author",), **kw) -> dict:
    return {"title": title, "authors": list(authors), **kw}


# --------------------------------------------------------------------------
# title matching
# --------------------------------------------------------------------------

def test_title_similarity_ignores_accents_and_case():
    """It compares NORMALISED titles. Raw comparison scored `Pólya` against
    `Polya` as a mismatch, which is a spelling difference between editions rather
    than a different book."""
    assert ol.title_similarity("How to Solve It", "how to solve it") == 1.0
    assert ol.title_similarity("Pólya", "Polya") == 1.0


def test_the_best_of_every_title_form_is_used():
    """Which field carries the subtitle is inconsistent across editions: one puts
    it in `full_title`, another in `subtitle`, another folds it into `title`. So
    all three forms are scored and the best wins — otherwise a correct match
    against the bare title is thrown away by a long `full_title`."""
    row = {"id": 1, "title": "Statistical Rethinking", "authors": "Richard McElreath", "isbn": "x"}
    client = FakeClient(
        {"x": _edition(
            title="Statistical Rethinking",
            full_title="Statistical Rethinking: A Bayesian Course with Examples in R and Stan",
            authors=["Richard McElreath"],
        )}
    )
    out = ol.enrich(row, client)
    assert out["status"] == "strong-candidate"
    assert out["title_similarity"] == 1.0


def test_a_subtitle_only_edition_still_matches():
    """The `title: subtitle` form is constructed for exactly this shape."""
    row = {"id": 1, "title": "Algebra: Chapter 0", "authors": "Paolo Aluffi", "isbn": "x"}
    client = FakeClient(
        {"x": _edition(title="Algebra", subtitle="Chapter 0", authors=["Paolo Aluffi"])}
    )
    assert ol.enrich(row, client)["status"] == "strong-candidate"


def test_a_different_book_at_the_same_isbn_is_sent_to_review():
    """Wrong-ISBN-on-the-copyright-page is common, and it resolves to a real
    edition of something else. Silently accepting it would overwrite good
    metadata with another book's."""
    row = {"id": 1, "title": "Introduction to Logic", "authors": "Irving Copi", "isbn": "x"}
    client = FakeClient({"x": _edition(title="Stage Makeup", authors=["Richard Corson"])})
    out = ol.enrich(row, client)
    assert out["status"] == "review-title-author"


def test_a_matching_title_with_a_different_author_goes_to_review():
    """The failure `title_groups` exists for, in the other direction: Lang and
    Artin both wrote *Algebra*."""
    row = {"id": 1, "title": "Algebra", "authors": "Serge Lang", "isbn": "x"}
    client = FakeClient({"x": _edition(title="Algebra", authors=["Michael Artin"])})
    assert ol.enrich(row, client)["status"] == "review-title-author"


def test_a_record_with_no_author_passes_on_title_alone():
    """This is the population the tool exists to fix — a record with no author
    cannot fail an author check, and requiring one would exclude exactly the
    records that need help."""
    row = {"id": 1, "title": "A Book", "authors": "", "isbn": "x"}
    client = FakeClient({"x": _edition(title="A Book", authors=["Some Author"])})
    assert ol.enrich(row, client)["status"] == "strong-candidate"


def test_a_lookup_error_is_its_own_status():
    """Distinct from a bad match: nothing was learned, so the record is still a
    candidate next run. Collapsing it into `review` would bury it."""
    row = {"id": 1, "title": "A Book", "authors": "An Author", "isbn": "missing"}
    out = ol.enrich(row, FakeClient({}))
    assert out["status"] == "lookup-error"
    assert out["openlibrary"]["error"] == "http 404"
    # No score is invented for a lookup that did not happen.
    assert "title_similarity" not in out


# --------------------------------------------------------------------------
# author overlap
# --------------------------------------------------------------------------

def test_authors_split_on_ampersand_and_on_the_word_and():
    assert ol.split_authors("First Last & Second Person") == ["First Last", "Second Person"]
    assert ol.split_authors("First Last and Second Person") == ["First Last", "Second Person"]


def test_overlap_matches_on_surname_not_full_string():
    """Open Library writes `Donald Ervin Knuth` where Calibre has `Donald E.
    Knuth`. Full-string equality finds nothing."""
    assert ol.author_overlap("Donald E. Knuth", ["Donald Ervin Knuth"]) == 1


def test_two_unkeyable_names_do_not_overlap():
    """Empty keys are dropped. Without that, two unrelated CJK authors both keyed
    to "" and scored an overlap of 1 — which promoted a wrong edition to
    strong-candidate."""
    assert ol.author_overlap("村上春樹", ["曹雪芹"]) == 0


def test_an_absent_author_on_either_side_is_no_overlap():
    assert ol.author_overlap("", ["Someone"]) == 0
    assert ol.author_overlap("Someone", []) == 0


# --------------------------------------------------------------------------
# candidate selection
# --------------------------------------------------------------------------

def test_the_catalogues_own_isbn_wins_over_one_scraped_off_a_page():
    """A value in `identifiers` was entered deliberately; one found by regex on a
    copyright page was not. Both sources can name the same book id, so the
    precedence has to be explicit."""
    report = {
        "safe_existing_isbn_lookup": [
            {"id": 5, "title": "T", "authors": "A", "isbn": "9780262035613", "issues": []}
        ],
        "isbn_scan": [{"id": 5, "title": "T", "authors": "A", "isbns": ["9780131103627"]}],
    }
    rows = ol.candidate_rows(report)
    assert len(rows) == 1
    assert rows[0]["isbn"] == "9780262035613"
    assert rows[0]["source"] == "existing-calibre-isbn"


def test_a_single_scanned_isbn_becomes_a_candidate():
    report = {"isbn_scan": [{"id": 9, "title": "T", "authors": "A", "isbns": ["9780262035613"]}]}
    rows = ol.candidate_rows(report)
    assert rows[0]["source"] == "pdf-scan-single-isbn"
    assert rows[0]["issues"] == ["missing-isbn"]


def test_a_page_listing_several_isbns_is_skipped_by_default():
    """Usually the hardback/paperback/ebook of one work — but sometimes a series
    page advertising OTHER titles, and then picking the first is simply wrong."""
    report = {"isbn_scan": [{"id": 9, "title": "T", "authors": "A",
                             "isbns": ["9780262035613", "9780131103627"]}]}
    assert ol.candidate_rows(report) == []


def test_include_multi_takes_the_first_and_labels_it_a_guess():
    """The label is the point: the guess stays visible downstream instead of
    looking like a found value."""
    report = {"isbn_scan": [{"id": 9, "title": "T", "authors": "A",
                             "isbns": ["9780262035613", "9780131103627"]}]}
    rows = ol.candidate_rows(report, include_multi=True)
    assert rows[0]["source"] == "pdf-scan-multi-isbn-first"
    assert rows[0]["isbn"] == "9780262035613"
    assert rows[0]["all_found_isbns"] == ["9780262035613", "9780131103627"]


def test_an_empty_report_yields_no_candidates():
    assert ol.candidate_rows({}) == []


# --------------------------------------------------------------------------
# build — ordering and counts
# --------------------------------------------------------------------------

def test_build_sorts_strong_candidates_first_and_is_stable(monkeypatch):
    """Sorted by (status, id): `lookup-error` < `review-title-author` <
    `strong-candidate` alphabetically, so the sort is explicitly BY NAME and this
    test pins the resulting order rather than assuming it. Stability matters
    because an audit is diffed against the previous one."""
    editions = {
        "a": _edition(title="Exact Match", authors=["Ann Author"]),
        "b": _edition(title="Something Else Entirely", authors=["Zed"]),
    }
    monkeypatch.setattr(ol, "OpenLibraryClient", lambda timeout: FakeClient(editions))
    report = {
        "safe_existing_isbn_lookup": [
            {"id": 2, "title": "Exact Match", "authors": "Ann Author", "isbn": "a", "issues": []},
            {"id": 1, "title": "Different Book", "authors": "Ann Author",
             "isbn": "b", "issues": []},
            {"id": 3, "title": "Gone", "authors": "Ann Author", "isbn": "zzz", "issues": []},
        ]
    }
    out = ol.build(report, workers=2)
    assert [(r["status"], r["id"]) for r in out["candidates"]] == [
        ("lookup-error", 3),
        ("review-title-author", 1),
        ("strong-candidate", 2),
    ]
    assert out["summary"] == {
        "input_candidates": 3,
        "lookup-error": 1,
        "review-title-author": 1,
        "strong-candidate": 1,
    }


def test_build_respects_the_limit(monkeypatch):
    monkeypatch.setattr(ol, "OpenLibraryClient", lambda timeout: FakeClient({}))
    report = {
        "safe_existing_isbn_lookup": [
            {"id": i, "title": "T", "authors": "A", "isbn": f"i{i}", "issues": []}
            for i in range(1, 6)
        ]
    }
    assert ol.build(report, limit=2)["summary"]["input_candidates"] == 2


def test_progress_is_optional(monkeypatch):
    """`build` is also called as a library function; a hard-coded print would
    write to a caller's stdout."""
    monkeypatch.setattr(ol, "OpenLibraryClient", lambda timeout: FakeClient({}))
    seen: list[tuple[int, int]] = []
    report = {"safe_existing_isbn_lookup": [
        {"id": 1, "title": "T", "authors": "A", "isbn": "x", "issues": []}
    ]}
    ol.build(report, on_progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 1)]
    ol.build(report)  # no callback -> no output, no crash


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_markdown_escapes_pipes_so_the_table_keeps_its_shape(tmp_path):
    """A title containing `|` silently adds columns and shifts every value right
    of it into the wrong header."""
    report = {
        "summary": {"input_candidates": 1},
        "candidates": [{
            "id": 1, "status": "strong-candidate", "source": "existing-calibre-isbn",
            "isbn": "9780262035613", "title": "Before | After",
            "openlibrary": _edition(title="Before | After", authors=["A"]),
        }],
    }
    out = tmp_path / "c.md"
    ol.write_markdown(report, out)
    row = next(line for line in out.read_text().splitlines() if line.startswith("| 1 |"))
    assert r"Before \| After" in row
    # Count DELIMITERS, not `|` characters — an escaped pipe still contains one,
    # which is what makes this bug easy to "verify" wrongly.
    assert row.replace(r"\|", "").count("|") == 10   # 9 columns -> 10 delimiters
    header = next(line for line in out.read_text().splitlines() if line.startswith("| ID |"))
    assert header.count("|") == 10, "the row and header must agree on column count"


def test_markdown_handles_a_lookup_error_row_with_no_edition(tmp_path):
    """`openlibrary` carries only an `error` key on a failed lookup; every getter
    in the renderer has to tolerate that or the report dies on the last step."""
    report = {
        "summary": {"input_candidates": 1},
        "candidates": [{
            "id": 1, "status": "lookup-error", "source": "pdf-scan-single-isbn",
            "isbn": "9780262035613", "title": "A Book",
            "openlibrary": {"isbn": "9780262035613", "error": "http 404"},
        }],
    }
    out = tmp_path / "c.md"
    ol.write_markdown(report, out)
    assert "lookup-error" in out.read_text()


@pytest.mark.parametrize("authors,expected", [
    ([], ""),
    (["A"], "A"),
    (["A", "B", "C"], "A & B & C"),
    (["A", "B", "C", "D", "E"], "A & B & C & +2"),
])
def test_long_author_lists_are_truncated_with_a_count(authors, expected):
    """An Open Library edition can list a dozen contributors, which turns one
    table row into a wrapped paragraph."""
    assert ol.short_authors(authors) == expected


# --------------------------------------------------------------------------
# The CJK / accent regressions, ported from omni-rag's
# tests/test_openlibrary_candidate_matching.py when this module moved here.
#
# `norm` and `author_surname` themselves are covered by test_normalize.py. What
# is NOT covered there, and lives only here, is what the two COMPOSITES do with
# their output: `title_similarity` feeds `norm` into SequenceMatcher, and
# `author_overlap` feeds `author_surname` into a SET INTERSECTION. Both turn an
# empty normaliser result into a false POSITIVE, which is the dangerous
# direction — it promotes a wrong edition to `strong-candidate`.
# --------------------------------------------------------------------------

def test_two_unrelated_cjk_titles_are_not_a_perfect_match():
    """The sharp end of the CJK bug. A blanket `[^a-z0-9]+` mapped every CJK title
    to '', and SequenceMatcher rates '' against '' as 1.000 — so two entirely
    different Chinese books scored a PERFECT title match."""
    assert (
        ol.title_similarity("陶哲轩实分析", "人体结构与动态绘制高效练习法")
        < ol.TITLE_MATCH_THRESHOLD
    )


def test_identical_cjk_titles_still_match():
    """The other half: preserving CJK must not cost real matches."""
    assert ol.title_similarity("陶哲轩实分析", "陶哲轩实分析") == 1.0


def test_an_accent_difference_does_not_block_a_title_match():
    """Calibre and Open Library disagree about accents constantly. Asserted
    against the module's own threshold rather than a hardcoded number, so tuning
    the threshold cannot silently invalidate this."""
    assert (
        ol.title_similarity("Können: Ein Handbuch", "Konnen: Ein Handbuch")
        >= ol.TITLE_MATCH_THRESHOLD
    )


def test_an_accented_surname_is_not_reduced_to_one_letter():
    """`Juan M. Durán` keyed to the single letter 'n': the accent split the
    surname and 'n' became the last token."""
    assert ol.author_key("Juan M. Durán") == "duran"
    assert ol.author_key("Durán, Juan M.") == "duran"


def test_the_same_cjk_author_does_overlap():
    assert ol.author_overlap("陶哲轩", ["陶哲轩"]) == 1


def test_an_accented_author_matches_its_unaccented_open_library_spelling():
    """Before the fold, 'Hébert' keyed to 't' and 'Hebert' to 'hebert', so the
    same person did not match themselves across the two sources."""
    assert ol.author_overlap("Hébert", ["Hebert"]) == 1


def test_a_generational_suffix_alone_still_matches():
    assert ol.author_overlap("Richards J. Heuer Jr.", ["Richards J. Heuer"]) == 1


def test_punctuation_only_author_fields_do_not_match_each_other():
    """Both key to '', and `{''} & {''}` has size 1 — a false author match between
    two records that name no author at all."""
    assert ol.author_overlap("", [""]) == 0
    assert ol.author_overlap("...", ["???"]) == 0
