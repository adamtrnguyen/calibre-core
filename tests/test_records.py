"""Book model and query shape."""

from pathlib import Path

from calibre_core.records import books_by_tag, get_book, iter_tags, load_books


def test_formats_are_absolute_paths_not_format_codes(library):
    """The two repos disagreed on this field's shape. Paths win: a path reduces
    to its suffix, a code cannot be turned back into a path."""
    staged = library.add(1, "A Book", authors="An Author", fmt="PDF")
    b = get_book(1)
    assert b is not None
    assert isinstance(b.formats[0], Path)
    assert b.formats[0] == staged
    assert b.formats[0].is_absolute()


def test_authors_join_with_ampersand(library):
    library.add(1, "Two Authors", authors="First Person & Second Person")
    assert get_book(1).authors_str == "First Person & Second Person"


def test_sizes_come_from_the_catalogue_not_the_filesystem(library):
    """Reading sizes from `data.uncompressed_size` is what keeps duplicate
    detection free of I/O on a OneDrive library of dataless placeholders."""
    library.add(1, "Sized", content=b"%PDF-1.4\n" + b"x" * 100)
    assert get_book(1).sizes[0] == 109


def test_tags_and_lookup_are_case_insensitive(library):
    library.add(1, "Tagged", tags="color-and-light,reference")
    assert set(get_book(1).tags) == {"color-and-light", "reference"}
    assert [b.id for b in books_by_tag("Color-And-Light")] == [1]


def test_iter_tags_counts_and_respects_min_count(library):
    library.add(1, "A", tags="common,rare")
    library.add(2, "B", tags="common")
    assert dict(iter_tags()) == {"common": 2, "rare": 1}
    assert dict(iter_tags(min_count=2)) == {"common": 2}


def test_book_with_no_author_or_tags_still_loads(library):
    library.add(1, "Bare", authors="", tags="")
    b = get_book(1)
    assert b.authors == () and b.tags == () and b.authors_str == ""


def test_calibre_url_needs_a_uuid(library):
    library.add(1, "Linkable")
    assert "uuid-1" in get_book(1).calibre_url


def test_load_books_returns_every_book(library):
    for i in range(1, 6):
        library.add(i, f"Book {i}")
    assert len(load_books()) == 5
