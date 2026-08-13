"""Normaliser regression locks.

Each case here is a bug that was live in one of the three implementations this
module replaced, or a real false positive the old grouping produced against
Adam's library. Verified by a read-only differential over 1,091 books.
"""

import unicodedata

from calibre_core.normalize import author_surname, dedup_key, norm


def test_accents_fold_so_a_us_keyboard_reaches_them():
    assert norm("Können") == norm("Konnen")
    assert norm("Hébert") == norm("Hebert")


def test_cjk_survives_normalisation():
    """A `[^a-z0-9]` strip mapped every CJK title to '', and since callers filter
    empty keys those books vanished from duplicate detection entirely."""
    assert norm("陶哲轩实分析")
    assert dedup_key("陶哲轩实分析") != dedup_key("线性代数")


def test_hangul_survives_because_nfkd_decomposes_it_into_jamo():
    """Keeping Hangul in the allowed character class is NOT sufficient. A blanket
    NFKD decomposes 한 into conjoining Jamo (U+1112 U+1161 U+11AB) in the U+1100
    block, which the class does not cover -- so Korean still collapsed to ''
    while the test above passed.

    The evidence is synthetic on purpose: the library's one Korean title (393,
    'Daily Sketch: People (매일 스케치 인물)') keeps its Korean inside a
    parenthetical, which dedup_key strips by design, so that record normalised
    to 'daily sketch people' both before and after the fix. It is not a
    regression witness -- do not cite it as one."""
    assert norm("한국어 해부학")
    assert dedup_key("매일 스케치 인물") != dedup_key("한국어 해부학")


def test_hangul_survives_from_decomposed_input():
    """macOS stores filenames NFD, so a title can arrive already decomposed."""

    assert norm(unicodedata.normalize("NFD", "한국어")) == norm("한국어")


def test_kana_voicing_marks_are_not_stripped_as_diacritics():
    """NFKD splits the dakuten off as a combining mark and the diacritic strip
    then deletes it, turning デザイン into テサイン and が into か. Voicing is
    phonemic -- that is a different word, and it makes distinct titles collide.
    Live on library books 922 (ドキドキ) and 923 (が)."""
    assert "ドキドキ" in norm("ちょっとドキドキする女の子")
    assert norm("が") != norm("か")
    assert norm("デザイン") != norm("テサイン")


def test_halfwidth_kana_folds_onto_fullwidth():
    """Non-CJK codepoints still go through NFKD, which is what makes halfwidth
    forms reachable from a fullwidth query."""
    assert norm("ｷｬﾗｸﾀｰ") == norm("キャラクター")


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
    # `X != a or True` was here, which no input can fail. drop_manual=True is
    # supposed to GROUP the pair, so assert that instead of a tautology.
    assert dedup_key("Introduction to Logic: Solutions Manual", drop_manual=True) == a


def test_surname_separates_same_title_different_author():
    """Lang vs Artin *Algebra* (ids 491/495) and Corson vs Buchman *Stage Makeup*
    (999/1056) are different books. Title alone made them duplicates."""
    assert author_surname("Serge Lang") != author_surname("Michael Artin")
    assert author_surname("Richard Corson & James Glavan") != author_surname("Herman Buchman")


def test_surname_takes_the_first_author_only():
    assert author_surname("Wayne C. Booth & Gregory G. Colomb") == "booth"


def test_hyphenated_surname_is_one_name():
    """`norm` turns the hyphen into a space, so taking the last token gave
    'dusseau' / 'brockmann' / 'mestre'. All three are real library authors."""
    assert author_surname("Remzi H. Arpaci-Dusseau & Andrea C. Arpaci-Dusseau") == "arpaci-dusseau"
    assert author_surname("Josef Müller-Brockmann") == "muller-brockmann"
    assert author_surname("Marcos Mateu-Mestre") == "mateu-mestre"


def test_surname_drops_initials_and_generational_suffixes():
    """Taking the last token made 'Richards J. Heuer Jr.' key to 'jr' -- which
    collides with every other Jr. in the library."""
    assert author_surname("Richards J. Heuer Jr.") == "heuer"
    assert author_surname("Juan M. Durán") == "duran"


def test_surname_handles_surname_first_form():
    assert author_surname("Lang, Serge") == author_surname("Serge Lang") == "lang"


def test_surname_still_discriminates_the_algebra_pair():
    """The reason this function exists: Lang and Artin both wrote *Algebra*."""
    assert author_surname("Serge Lang") != author_surname("Michael Artin")


def test_cjk_author_names_survive_and_stay_distinct():
    """The surname path is a SECOND site of the CJK bug, and it was introduced by
    the fix for the first: `_fold_diacritics` preserves CJK, then the cleanup
    class `[^a-z0-9\\s-]` wiped it again, so every CJK author keyed to ''.

    That is worse than a lost name. Callers compare surnames for EQUALITY as a
    duplicate discriminator, so two unrelated CJK authors both keying to ''
    compare equal. Real library authors: 蒙小洛 (803), 陶哲轩 (25)."""
    assert author_surname("陶哲轩") == "陶哲轩"
    assert author_surname("蒙小洛") == "蒙小洛"
    assert author_surname("김정호") == "김정호"
    assert author_surname("陶哲轩") != author_surname("夏目漱石")


def test_single_character_cjk_surname_is_not_dropped():
    """The len>1 filter that removes middle initials must not eat a one-glyph
    name; the fallback to the raw token list covers it."""
    assert author_surname("李") == "李"


def test_apostrophe_surnames_keep_their_leading_particle():
    """Found by diffing this function against `calibre-check-wip`'s `surname` over
    all 1388 real authors — the one case where that unshipped version was right and
    this was wrong.

    The cleanup class turned the apostrophe into a SPACE, splitting `O'Keefe` into
    tokens 'o' and 'keefe'; 'o' is then discarded by the len>1 middle-initial
    filter, so the surname came back as 'keefe'. Four real authors were affected.
    An apostrophe joins one name, exactly like the hyphen already does."""
    assert author_surname("Daniel J. O'Keefe") == "okeefe"
    assert author_surname("David R. O'Hallaron") == "ohallaron"
    assert author_surname("Joseph D'Amelio") == "damelio"
    assert author_surname("Marie O'Mahony") == "omahony"


def test_curly_apostrophe_keys_the_same_as_a_straight_one():
    """Calibre stores whatever the metadata source pasted in, and a right single
    quote is what most web sources emit. Two spellings of one author must not be
    two different surnames — that is a missed duplicate, not a cosmetic issue."""
    assert author_surname("Daniel J. O’Keefe") == author_surname("Daniel J. O'Keefe")


def test_stroke_letters_do_not_truncate_the_surname():
    """`ø` and `ł` carry their diacritic INSIDE the codepoint, so NFKD does not
    decompose them and the combining-mark strip cannot reach them. They then hit
    the punctuation class and became a space, which SPLIT the word: `Nørsett` keyed
    to 'rsett' and `Łupkowski` to 'upkowski' — the surname's leading letter gone.

    Real library authors: Syvert P. Nørsett (Hairer & Nørsett & Wanner), Paweł
    Łupkowski. Neither implementation diffed against got this right; the E_work one
    returned 'nrsett', which is merely less wrong."""
    assert author_surname("Syvert P. Nørsett") == "norsett"
    assert author_surname("Paweł Łupkowski") == "lupkowski"
    assert author_surname("Øystein Linnebo") == "linnebo"


def test_stroke_letters_fold_in_titles_too_so_search_reaches_them():
    """The fix lives in `_fold_diacritics`, which is shared, so a US-keyboard query
    now reaches these the same way it already reached Können and Hébert. Zero real
    titles contain these characters, so this changes no duplicate grouping — it is
    the search-reach half of the same defect."""
    assert norm("Nørsett") == "norsett"
    assert norm("Łódź") == "lodz"
    assert dedup_key("Straße") == "strasse"


def test_stroke_fold_runs_after_decomposition_not_before():
    """`ǿ` (U+01FF) decomposes to `ø` + acute. Translating before the
    combining-mark strip would miss it and leave a bare `ø` for the character class
    to eat, reintroducing the truncation this fixes."""
    assert norm("ǿrn") == "orn"


def test_ligature_folds_expand_to_two_letters():
    """`æ`/`œ`/`ß` are one codepoint standing for two letters, so a 1:1 map would
    silently shorten the word. NFKD does not expand them (only NFKC_CF does), which
    is why they need the explicit map."""
    assert norm("Æsop") == "aesop"
    assert norm("Œuvres") == "oeuvres"
