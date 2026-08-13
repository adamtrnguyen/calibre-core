"""Duplicate detection, tiered by whether human judgement is required.

Three independent signals, because each catches what the others cannot:

  sha256 / exact size  -- the ONLY signal that finds the same file catalogued
                          twice under two different titles. Found exactly that in
                          the real library: ids 954 and 995, identical
                          sha256, 49,737,184 bytes, long-form vs short-form
                          title, ~50 MB wasted and invisible to every
                          title-based check.
  ISBN                  -- same edition, stated by the publisher.
  title + surname       -- plausible; needs a human, because it cannot separate
                          two editions from two copies.

Nothing here deletes. `#dupok` suppression exists so that a pair which is
deliberate stops being reported on every run — an unsuppressible false positive
is how a human learns to ignore the output.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from calibre_core.isbn import clean_isbn
from calibre_core.library import connect, custom_column_id
from calibre_core.normalize import author_surname, dedup_key
from calibre_core.records import Book, load_books


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def dupok_pairs(db: Path | None = None) -> dict[int, set[int]]:
    """book id -> ids it is explicitly allowed to duplicate.

    Stored in the `#dupok` custom column rather than a file, so the exemption
    travels with the record and is visible in the Calibre GUI. A markdown
    allowlist drifted: 4 of its 7 entries named pairs that never grouped, while
    it missed all four series that did.
    """
    cid = custom_column_id("dupok", db)
    if cid is None:
        return {}
    out: dict[int, set[int]] = defaultdict(set)
    con = connect(db)
    try:
        rows = con.execute(
            f"SELECT l.book, v.value FROM books_custom_column_{cid}_link l "
            f"JOIN custom_column_{cid} v ON v.id = l.value"
        ).fetchall()
    except sqlite3.Error:
        # The custom_column_<id> tables only exist once the column has been
        # created; a missing table means "no exemptions recorded", not a bug.
        # Narrow deliberately: a blind except here would swallow real errors.
        return {}
    finally:
        con.close()
    for book, val in rows:
        for part in str(val).replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                out[book].add(int(part))
    return dict(out)


def excused(a: int, b: int, pairs: dict[int, set[int]]) -> bool:
    """Is this specific PAIR excused? Symmetric, and fails OPEN.

    One side naming the other is enough. Requiring both would mean a human edits
    two records to silence one pair, and the pair keeps being reported until they
    do -- which is how the output stops being read.

    Public because it was private for one release and two consumers wrote their
    own instead (`calibre_mcp.writes._excused_within`, and omni-rag's audit
    `_all_excused`). A pairwise predicate is not an implementation detail when
    every caller that reports duplicates needs it.
    """
    return b in pairs.get(a, set()) or a in pairs.get(b, set())


# The name this shipped under in 0.1.0. Kept so existing importers do not break.
_excused = excused


def excused_within(
    book_id: int, candidates: Iterable[int], pairs: dict[int, set[int]]
) -> bool:
    """Is `book_id` excused against at least one OTHER member of `candidates`?

    The helper both consumers actually wanted, and the reason it is here rather
    than in each of them: suppression must require the two records be paired with
    EACH OTHER, not merely both appear somewhere in the `#dupok` column.

    `calibre_mcp.writes` got that wrong by flattening every partner into one
    global set and suppressing on mere membership. So if any unrelated record
    named two books as its partners, a genuine duplicate BETWEEN those two books
    reported `ok` -- the write gate waved through exactly the duplicate it exists
    to catch. Nothing reported that it had.

    It was then fixed locally, as `writes._excused_within`, and omni-rag's audit
    grew its own. This function is the shared version those two copies collapse
    into; the semantics here are the corrected ones, not the flattened ones.

    `candidates` is the matched set, and `book_id` is excluded from it here rather
    than by the caller: a record trivially "matches itself" in any set built from
    its own signals, and self-pairing would excuse every record from everything.
    """
    return any(excused(book_id, other, pairs) for other in candidates if other != book_id)


def title_groups(
    books: list[Book] | None = None,
    db: Path | None = None,
    *,
    respect_dupok: bool = True,
) -> list[list[Book]]:
    """Group by normalised title + first-author surname.

    Surname is part of the key deliberately: on title alone, Lang and Artin's
    *Algebra* are duplicates. The subtitle is kept, so series volumes stay apart.
    """
    books = books if books is not None else load_books(db)
    pairs = dupok_pairs(db) if respect_dupok else {}
    buckets: dict[tuple[str, str], list[Book]] = defaultdict(list)
    for b in books:
        k = dedup_key(b.title)
        if not k:
            continue
        buckets[(k, author_surname(b.authors_str))].append(b)
    groups = []
    for grp in buckets.values():
        if len(grp) < 2:
            continue
        # EVERY pair, not any: a three-book group with one excused pair still has
        # a real duplicate in it, so it must still be reported. (`excused_within`
        # is the ANY form, for the different question a single-record check asks.)
        if respect_dupok and all(
            excused(x.id, y.id, pairs) for x in grp for y in grp if x.id != y.id
        ):
            continue
        groups.append(sorted(grp, key=lambda b: b.id))
    return sorted(groups, key=lambda g: g[0].id)


def size_groups(
    books: list[Book] | None = None,
    db: Path | None = None,
    *,
    respect_dupok: bool = True,
) -> list[list[Book]]:
    """Group by identical format byte size, read from `data.uncompressed_size`.

    Zero file reads: the size is a catalogue column. That matters because the
    library is on OneDrive where most files are dataless placeholders and reading
    one byte hydrates the whole file, so a hashing sweep would be a multi-GB
    download. Hash only these candidates, and only if you need certainty.
    """
    books = books if books is not None else load_books(db)
    pairs = dupok_pairs(db) if respect_dupok else {}
    buckets: dict[int, list[Book]] = defaultdict(list)
    for b in books:
        for s in b.sizes:
            if s:
                buckets[s].append(b)
    groups = []
    for grp in buckets.values():
        uniq = {b.id: b for b in grp}
        if len(uniq) < 2:
            continue
        ids = list(uniq)
        if respect_dupok and all(
            excused(x, y, pairs) for x in ids for y in ids if x != y
        ):
            continue
        groups.append([uniq[i] for i in sorted(uniq)])
    return sorted(groups, key=lambda g: g[0].id)


def isbn_groups(
    books: list[Book] | None = None, db: Path | None = None
) -> list[list[Book]]:
    """Group by identical cleaned ISBN — same edition, per the publisher."""
    books = books if books is not None else load_books(db)
    buckets: dict[str, list[Book]] = defaultdict(list)
    for b in books:
        c = clean_isbn(b.isbn)
        if c:
            buckets[c].append(b)
    return sorted(
        (sorted(g, key=lambda b: b.id) for g in buckets.values() if len(g) > 1),
        key=lambda g: g[0].id,
    )
