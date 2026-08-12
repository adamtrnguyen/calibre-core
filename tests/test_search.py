"""Search behaviour, now backed by rapidfuzz Indel.

The structure (per-query-token best-match averaging) is deliberately NOT
rapidfuzz's token_set_ratio. Measured over 1,091 real books, swapping to any
single rapidfuzz scorer reproduced only 6/8 golden result sets and collapsed
`perspektive` from 10 hits to 2. Keeping the structure and swapping only the
primitive preserves 7/8, losing nothing.
"""

from calibre_core.search import score, search, token_set_ratio


def test_substring_is_a_free_win():
    assert score("perspective", "basic perspective drawing") == 1.0


def test_typo_still_scores_high_enough_to_be_found():
    """The flagship case: one substituted character must not sink the score.

    Indel was chosen over Levenshtein and Jaro-Winkler precisely because it keeps
    this above the 0.55 threshold.
    """
    assert score("perspektive", "basic perspective drawing") >= 0.55


def test_token_averaging_lets_a_short_query_reach_a_long_title():
    """'geometry of art' -> 'The Geometry of an Art'. rapidfuzz's own
    token_set_ratio dilutes this; the averaging here does not."""
    assert token_set_ratio("geometry of art", "the geometry of an art") > 0.9


def test_empty_candidate_scores_zero():
    assert score("anything", "") == 0.0


def test_empty_query_returns_nothing(library):
    library.add(1, "A Book")
    assert search("", []) == []
    assert search("   ", []) == []


def test_ranking_is_best_first_and_deterministic(library):
    for i, t in enumerate(["Perspective Drawing", "Perspective", "Unrelated Cooking"], start=1):
        library.add(i, t)
    from calibre_core.records import load_books

    res = search("perspective", load_books())
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    # Ties break on id, so repeated calls cannot reorder.
    assert res == search("perspective", load_books())


def test_margin_drops_the_noisy_tail(library):
    from calibre_core.records import load_books

    library.add(1, "Kirsti Andersen Geometry")
    library.add(2, "Kristin Neff Self Compassion")
    tight = search("kirsti andersen", load_books())
    loose = search("kirsti andersen", load_books(), margin=1.0)
    assert len(tight) <= len(loose)


def test_field_restriction(library):
    from calibre_core.records import load_books

    library.add(1, "Optics", authors="Eugene Hecht")
    books = load_books()
    assert search("hecht", books, field="authors")
    assert not search("hecht", books, field="title")
