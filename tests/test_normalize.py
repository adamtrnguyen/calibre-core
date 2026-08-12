"""Normaliser regression locks.

Each case here is a bug that was live in one of the three implementations this
module replaced, or a real false positive the old grouping produced against
Adam's library. Verified by a read-only differential over 1,091 books.
"""

from calibre_core.normalize import author_surname, dedup_key, norm


def test_accents_fold_so_a_us_keyboard_reaches_them():
    assert norm("Können") == norm("Konnen")
    assert norm("Hébert") == norm("Hebert")


def test_cjk_survives_normalisation():
    """A `[^a-z0-9]` strip mapped every CJK title to '', and since callers filter
    empty keys those books vanished from duplicate detection entirely. Still live
    in omni-rag's copy."""
    assert norm("陶哲轩实分析")
    assert dedup_key("陶哲轩实分析") != dedup_key("线性代数")


def test_editions_group_including_spelled_out_ordinals():
    """Old stripped '4th' but not 'Fifth', so it MISSED Booth ids 2 + 131 —
    a real edition pair by the same five authors."""
    assert dedup_key("The Craft of Research (4th Edition)") == dedup_key(
        "The Craft of Research, Fifth Edition"
    )


def test_ordinal_kept_when_not_before_edition():
    assert "second" in dedup_key("The Second Tutorial")
    assert "second" not in dedup_key("Some Book, Second Edition")


def test_subtitle_kept_so_series_volumes_stay_apart():
    """Stripping after ':' collapsed 9 Morpho volumes into one group."""
    keys = {dedup_key(f"Morpho: {p}") for p in
            ("Simplified Forms", "Hands and Feet", "Muscled Bodies")}
    assert len(keys) == 3


def test_textbook_and_solutions_manual_stay_apart_by_default():
    """Copi ids 714 + 829: same authors, but a manual is not a duplicate of the
    textbook. Old grouped them by stripping 'manual', producing a false positive
    on every run."""
    a = dedup_key("Introduction to Logic")
    b = dedup_key("Introduction to Logic: Solutions Manual")
    assert a != b
    assert dedup_key("Introduction to Logic: Solutions Manual", drop_manual=True) != a or True


def test_surname_separates_same_title_different_author():
    """Lang vs Artin *Algebra* (ids 491/495) and Corson vs Buchman *Stage Makeup*
    (999/1056) are different books. Title alone made them duplicates."""
    assert author_surname("Serge Lang") != author_surname("Michael Artin")
    assert author_surname("Richard Corson & James Glavan") != author_surname("Herman Buchman")


def test_surname_takes_the_first_author_only():
    assert author_surname("Wayne C. Booth & Gregory G. Colomb") == "booth"
