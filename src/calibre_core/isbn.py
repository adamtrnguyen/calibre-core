"""ISBN cleaning and checksum validation.

Lifted from omni-rag's calibre_metadata_audit.py so both repos validate the same
way. A checksum matters here: an ISBN that merely looks well-formed is the kind
of metadata that gets trusted downstream and then silently identifies the wrong
edition.
"""

from __future__ import annotations

import re


def clean_isbn(raw: str | None) -> str:
    """Strip hyphens, spaces and any prefix, upcasing a trailing check 'x'."""
    if not raw:
        return ""
    s = re.sub(r"(?i)^isbn[-: ]*", "", str(raw).strip())
    return re.sub(r"[^0-9Xx]", "", s).upper()


def valid_isbn10(s: str) -> bool:
    if len(s) != 10 or not re.fullmatch(r"[0-9]{9}[0-9X]", s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def valid_isbn13(s: str) -> bool:
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(s))
    return total % 10 == 0


def valid_isbn(raw: str | None) -> bool:
    s = clean_isbn(raw)
    return valid_isbn10(s) or valid_isbn13(s)
