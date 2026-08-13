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


# --------------------------------------------------------------------------
# publisher -- the field whose absence kept a consumer on `calibredb list`
# --------------------------------------------------------------------------

def test_publisher_is_read_from_the_link_table(library):
    """The gap that blocked omni-rag's metadata audit. `missing-publisher` is its
    second-biggest finding (641 of 1122 real records), and because `Book` had no
    `publisher` the whole audit sourced from `calibredb list --for-machine` — a
    subprocess against the live library — for this one field."""
    library.add(1, "Published Book", publisher="Princeton University Press")
    assert get_book(1).publisher == "Princeton University Press"


def test_publisher_is_none_when_absent_not_empty_string(library):
    """641 real records have no publisher row at all. `None` distinguishes
    "no publisher recorded" from a publisher recorded as blank; a caller testing
    `missing-publisher` wants both to be missing, but only one is a data entry
    error worth chasing."""
    library.add(1, "Unpublished Book")
    assert get_book(1).publisher is None


def test_a_publisher_shared_by_two_books_is_interned_not_duplicated(library):
    """Calibre stores the publisher NAME once in `publishers` and links to it, so
    the query must join rather than read a per-book column. A test with one book
    would pass against a fabricated `books.publisher` column too — the real schema
    has none."""
    library.add(1, "First", publisher="Springer")
    library.add(2, "Second", publisher="Springer")
    assert [b.publisher for b in load_books()] == ["Springer", "Springer"]


def test_publisher_does_not_multiply_rows_like_a_fan_out_join(library):
    """`books_publishers_link` carries `UNIQUE(book)`, so one publisher per book —
    but the same mistake that a naive authors/formats join makes (multiplied rows)
    would show up here as a duplicated Book. Asserted with tags and two authors
    present, which are the real fan-outs."""
    library.add(1, "Fanned", authors="A Writer & B Writer", tags="one,two",
                publisher="MIT Press")
    books = load_books()
    assert len(books) == 1
    assert books[0].publisher == "MIT Press"
    assert len(books[0].authors) == 2


def test_publisher_did_not_shift_the_other_positional_fields(library):
    """`Book` fields are positional as well as keyword, so `publisher` was appended
    rather than filed next to `isbn`. If it were inserted mid-list, `load_books`
    would still be correct (it passes keywords) while any caller constructing a
    Book positionally would silently get `publisher` in `path` — and a wrong uuid
    breaks calibre:// deep links with no error."""
    library.add(1, "Ordered", isbn="9780367860271", publisher="Routledge")
    b = get_book(1)
    assert b.uuid == "uuid-1"
    assert b.path.endswith("(1)")
    assert b.isbn == "9780367860271"
    assert b.publisher == "Routledge"
