"""ISBN handling, now delegated to isbnlib.

Kept as tests rather than trusting the library blind, because the wrapper adds
prefix-tolerance that isbnlib does not do on its own, and because a checksum that
silently passes on a corrupt ISBN would identify the wrong edition downstream.
"""

import pytest

from calibre_core.isbn import (
    clean_isbn,
    hyphenate,
    to_isbn13,
    valid_isbn,
    valid_isbn10,
    valid_isbn13,
)

# Real ISBNs from books acquired this session.
YEARWORTH_13 = "9781119744825"
HUNTER_10 = "0471830062"
LAMBOURNE_10 = "185573348X"      # trailing X check digit
GIBSON_13 = "9783030561260"


@pytest.mark.parametrize("raw", [
    YEARWORTH_13,
    "978-1-119-74482-5",
    "978 1 119 74482 5",
    "ISBN: 978-1-119-74482-5",
    "isbn-13: 9781119744825",
    "  9781119744825  ",
])
def test_prefix_and_punctuation_are_tolerated(raw):
    """This is what the wrapper adds — isbnlib.canonical alone keeps the prefix."""
    assert clean_isbn(raw) == YEARWORTH_13


def test_a_corrupt_check_digit_is_rejected():
    """The last digit is deliberately wrong. Well-formed is not the same as valid."""
    assert valid_isbn(YEARWORTH_13)
    assert not valid_isbn("9781119744826")


def test_both_isbn_lengths_validate():
    assert valid_isbn13(YEARWORTH_13) and not valid_isbn10(YEARWORTH_13)
    assert valid_isbn10(HUNTER_10) and not valid_isbn13(HUNTER_10)


def test_trailing_x_check_digit():
    assert valid_isbn(LAMBOURNE_10)
    assert clean_isbn("1-85573-348-x") == LAMBOURNE_10


def test_to_isbn13_lets_editions_be_compared_across_forms():
    """A 10 and its 13 are the same edition; duplicate detection must see that."""
    assert to_isbn13(HUNTER_10) == "9780471830061"
    assert to_isbn13(YEARWORTH_13) == YEARWORTH_13


def test_hyphenate_is_display_only():
    assert hyphenate(GIBSON_13).count("-") >= 3
    assert clean_isbn(hyphenate(GIBSON_13)) == GIBSON_13


@pytest.mark.parametrize("junk", ["", None, "not an isbn", "12345", "abcdefghij"])
def test_junk_yields_empty_and_invalid(junk):
    assert clean_isbn(junk) == ""
    assert not valid_isbn(junk)
