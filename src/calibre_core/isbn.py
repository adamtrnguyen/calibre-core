"""ISBN cleaning and validation — a thin wrapper over `isbnlib`.

Previously hand-rolled: ~25 lines reimplementing the ISBN-10 and ISBN-13
checksums. isbnlib does that, plus canonicalisation and hyphenation, and is
maintained. Kept as a wrapper rather than used directly at call sites so the
tolerant input handling ("ISBN: 978-1-119-..." with prefix and punctuation) lives
in one place and callers do not each re-derive it.
"""

from __future__ import annotations

import re

import isbnlib


def clean_isbn(raw: str | None) -> str:
    """Canonical digits-only form, or '' if it is not an ISBN.

    Tolerates an `ISBN:` prefix, hyphens and spaces, which is how ISBNs actually
    arrive from copyright pages and scraped metadata. `isbnlib.canonical` alone
    does not strip a leading prefix.
    """
    if not raw:
        return ""
    s = re.sub(r"(?i)^\s*isbn(?:-1[03])?\s*[-: ]*", "", str(raw).strip())
    return isbnlib.canonical(s) or ""


def valid_isbn10(s: str) -> bool:
    return bool(isbnlib.is_isbn10(clean_isbn(s)))


def valid_isbn13(s: str) -> bool:
    return bool(isbnlib.is_isbn13(clean_isbn(s)))


def valid_isbn(raw: str | None) -> bool:
    """True if the checksum is correct in either form.

    A checksum matters here: an ISBN that merely looks well-formed gets trusted
    downstream and then identifies the wrong edition.
    """
    c = clean_isbn(raw)
    return bool(c) and (bool(isbnlib.is_isbn13(c)) or bool(isbnlib.is_isbn10(c)))


def to_isbn13(raw: str | None) -> str:
    """Upgrade an ISBN-10 to its ISBN-13 form, for comparing across editions."""
    c = clean_isbn(raw)
    if not c:
        return ""
    return isbnlib.to_isbn13(c) or c


def hyphenate(raw: str | None) -> str:
    """Publisher-group hyphenation, for display only. Never for comparison."""
    c = clean_isbn(raw)
    if not c:
        return ""
    return isbnlib.mask(c) or c
