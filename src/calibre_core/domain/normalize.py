"""The one title normaliser pair.

Three divergent implementations existed before this, and the differences were
silent:

  calibre-mcp `norm`               NFKD + casefold, CJK preserved  -- correct
  calibre-mcp `duplicate_groups`   .lower(), no NFKD, CJK preserved
      -> search and dedup disagreed on accents
  omni-rag `normalized_title`      .lower(), `[^a-z0-9]`, NO CJK
      -> every CJK title collapsed to '', and because the caller filtered
         `if key`, those books were dropped from duplicate detection entirely.
         Not mis-grouped -- absent. Real ids 25, 803, 921, 922 all keyed to ''.
         Fixed 2026-08-12; omni-rag now mirrors this module.

A fourth copy then turned up that no design doc had named:
`omni-rag/scripts/calibre_openlibrary_metadata_candidates.py`, matching Open
Library candidates on `[^a-z0-9]` with no NFKD -- so `Juan M. Durán` keyed to
the single letter 'n'. Four divergent copies is the argument for this package.

Two functions stay, because they are genuinely different jobs and collapsing
them changes search results:

    'The Geometry of an Art, 2nd Edition'
        norm      -> 'the geometry of an art 2nd edition'   (search reach)
        dedup_key -> 'geometry of art'                      (grouping)

`dedup_key` is defined as a composition over `norm`, so a fix to normalisation
cannot apply to one and miss the other.
"""

from __future__ import annotations

import re
import unicodedata

# Kana, CJK ext-A, CJK unified, Hangul. Stripping these is the bug described
# above; every regex here must keep them in the allowed class.
CJK = r"぀-ヿ㐀-䶿一-鿿가-힯"

_ORDINAL = (
    r"(?:\d+(?:st|nd|rd|th)"
    r"|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
)

_STOPWORDS = r"\b(?:the|a|an)\b"
_EDITION_NOISE = r"\b(?:edition|ed|revised|rev|si)\b"
_MANUAL = r"\b(?:solutions?|manual|instructor)\b"

_CJK_CHAR = re.compile(rf"[{CJK}]")

# Dropped from the tail when picking a surname, so 'Heuer Jr.' is not 'jr'.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq"})

# Latin letters whose diacritic is welded into the codepoint, so NFKD does NOT
# decompose them and the combining-mark strip below cannot reach them. They then
# hit the `[^a-z0-9...]` class as punctuation and became a SPACE, which SPLIT the
# word: `Nørsett` keyed to 'rsett' and `Łupkowski` to 'upkowski' -- the leading
# letter of the surname silently gone. Mapped explicitly instead.
_ATOMIC_FOLDS = str.maketrans({
    "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
    "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ħ": "h", "Ħ": "H",
    "ŧ": "t", "Ŧ": "T", "ı": "i", "ŋ": "n", "Ŋ": "N",
    "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss",
})

# Apostrophes are DELETED, not space-substituted, because an apostrophe inside a
# surname is part of ONE name -- the same argument the hyphen already won. As a
# space it split `O'Keefe` into 'o' + 'keefe', and 'o' is then dropped as a
# middle initial, so four real authors keyed to the wrong name entirely:
# O'Keefe->'keefe', O'Hallaron->'hallaron', D'Amelio->'amelio', O'Mahony->'mahony'.
# Curly and modifier-letter forms included: Calibre stores whatever was pasted in.
_APOSTROPHE = re.compile(r"['’ʼʾ′`]")


def _fold_diacritics(s: str) -> str:
    """Strip Latin diacritics WITHOUT touching CJK.

    Keeping CJK in the allowed class is not sufficient on its own: a blanket
    NFKD + combining-mark strip corrupts CJK before the class is ever applied.

    * Hangul syllables DECOMPOSE into conjoining Jamo (U+1100 block), which
      `CJK` does not cover -- so every Korean title still collapsed to '', the
      exact failure this module exists to prevent. `한` -> U+1112 U+1161 U+11AB.
    * Kana voicing marks decompose into a combining mark that the next line then
      DELETES: `デザイン` became `テサイン`, `が` became `か`. Voicing is phonemic,
      so that is a different word, and it lets distinct titles collide.

    So decompose per character and skip CJK codepoints entirely. Non-CJK still
    goes through NFKD, which is what folds Können -> Konnen and also maps
    halfwidth/fullwidth forms onto their canonical kana and Latin.

    NFC first because macOS stores filenames decomposed (NFD): recomposing
    restores Hangul syllables and kana voicing before anything else runs.

    `_ATOMIC_FOLDS` runs LAST, after the combining-mark strip, and the order is
    load-bearing: `ǿ` (U+01FF) decomposes to `ø` + acute, so translating first
    would miss it and leave a `ø` behind for the character class to eat.
    """
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", s or ""):
        if _CJK_CHAR.match(ch):
            out.append(ch)
            continue
        d = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in d if not unicodedata.combining(c))
        out.append(stripped.translate(_ATOMIC_FOLDS))
    return "".join(out)


def norm(s: str) -> str:
    """Casefold, strip diacritics, drop punctuation. CJK codepoints survive.

    Andersen / Können / Bläsing / Hébert all fold to unaccented forms, so a query
    typed on a US keyboard reaches them.
    """
    return re.sub(rf"[^a-z0-9{CJK}]+", " ", _fold_diacritics(s).casefold()).strip()


def dedup_key(title: str, *, drop_manual: bool = False) -> str:
    """Normalise a title for duplicate GROUPING.

    Differences from `norm`, each one load-bearing:

    * Parentheticals and bracketed suffixes go, so '(4th Edition)' and
      '[Instructor Manual]' do not keep two records apart.
    * An ordinal is removed ONLY when it immediately precedes 'edition'. Removing
      ordinals generally would strip the number from 'The Second Tutorial', which
      is part of the title.
    * The SUBTITLE IS KEPT. Stripping everything after ':' collapsed nine Morpho
      volumes into a single duplicate group -- 11 bogus groups over 32 records in
      the real library -- because volume numbers live in the subtitle.
    * Leading articles and edition words go, so 'The Craft of Research (4th
      Edition)' and 'The Craft of Research, Fifth Edition' group as one work for
      review.

    drop_manual=True additionally strips 'solutions'/'manual'/'instructor', which
    makes a textbook and its solutions manual group together. That is off by
    default: those are legitimately different books, and grouping them
    guarantees a false positive on every single run, which is how a human learns
    to stop reading the output.
    """
    s = _fold_diacritics(title).casefold()
    s = re.sub(r"\(.*?\)|\[.*?\]|（.*?）", " ", s)
    s = re.sub(rf"{_ORDINAL}\s+edition\b", " ", s)
    s = re.sub(_EDITION_NOISE, " ", s)
    if drop_manual:
        s = re.sub(_MANUAL, " ", s)
    s = re.sub(_STOPWORDS, " ", s)
    return re.sub(rf"[^a-z0-9{CJK}]+", " ", s).strip()


def author_surname(authors: str) -> str:
    """First author's surname, casefolded — the discriminator dedup pairs on.

    Grouping on title alone makes Lang and Artin's *Algebra* a duplicate.

    A hyphenated surname is ONE name. Taking the last whitespace token of a
    normalised string returned 'dusseau' for Arpaci-Dusseau, 'brockmann' for
    Müller-Brockmann and 'mestre' for Mateu-Mestre, because `norm` turns the
    hyphen into a space. As a discriminator that errs permissive, which is the
    safe direction for a report, but it is still the wrong name -- and a released
    tag would freeze it. So the hyphen is kept here.

    Also handles 'Surname, First' (surname-first) and drops middle initials and
    generational suffixes: 'Richards J. Heuer Jr.' -> 'heuer', where taking the
    last token gave 'jr'.

    An APOSTROPHE is deleted rather than turned into a space, for the same reason
    the hyphen is kept: it joins one name. Space-substituting it split `O'Keefe`
    into 'o' + 'keefe', and the 'o' was then discarded as a middle initial, so the
    surname came back as 'keefe'. Four real authors were affected -- O'Keefe,
    O'Hallaron, D'Amelio, O'Mahony. Deletion (rather than keeping the mark) is
    what `calibre-check-wip`'s `surname` got right and this did not; it also means
    a record punctuated `OKeefe` still keys the same.
    """
    first = (authors or "").split("&")[0].strip()
    if not first:
        return ""
    if "," in first:
        first = first.split(",", 1)[0]
    s = _APOSTROPHE.sub("", _fold_diacritics(first)).casefold()
    # {CJK} belongs here for the same reason it belongs in norm: without it this
    # line wipes what _fold_diacritics just protected, and EVERY CJK author keys
    # to ''. That is worse than a lost name -- callers compare surnames for
    # equality, so two unrelated CJK authors both keying to '' compare EQUAL.
    # A CJK personal name has no whitespace-separated surname, so the whole
    # string is the key; that is consistent, which is all a discriminator needs.
    s = re.sub(rf"[^a-z0-9\s{CJK}-]+", " ", s)
    tokens = [t.strip("-") for t in s.split() if t.strip("-")]
    meaningful = [t for t in tokens if len(t) > 1 and t not in _SUFFIXES]
    return (meaningful or tokens)[-1] if tokens else ""
