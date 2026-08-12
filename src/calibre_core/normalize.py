"""The one title normaliser pair.

Three divergent implementations existed before this, and the differences were
silent:

  calibre-mcp `norm`               NFKD + casefold, CJK preserved  -- correct
  calibre-mcp `duplicate_groups`   .lower(), no NFKD, CJK preserved
      -> search and dedup disagreed on accents
  omni-rag `normalized_title`      .lower(), `[^a-z0-9]`, NO CJK
      -> every CJK title collapsed to '', and because the caller filtered
         `if key`, those books were dropped from duplicate detection entirely.
         Not mis-grouped -- absent. That bug is still live in omni-rag.

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


def norm(s: str) -> str:
    """Casefold, strip diacritics, drop punctuation. CJK codepoints survive.

    Andersen / Können / Bläsing / Hébert all fold to unaccented forms, so a query
    typed on a US keyboard reaches them.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    return re.sub(rf"[^a-z0-9{CJK}]+", " ", s).strip()


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
    s = unicodedata.normalize("NFKD", title or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
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
    """
    first = (authors or "").split("&")[0].strip()
    if not first:
        return ""
    return norm(first).split()[-1] if norm(first) else ""
