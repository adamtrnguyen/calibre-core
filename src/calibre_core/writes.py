"""Gated write path for the Calibre library.

Every add and metadata change should go through here rather than a bare
`calibredb` call, because the preconditions are easy to forget and expensive to
get wrong. The gates are enforced, not documented:

  * the Calibre GUI must be closed  (calibredb corrupts state if it is open)
  * metadata.db is backed up first  (a rollback is the only undo that exists)
  * duplicates are detected BEFORE the record is created
  * `title` and `authors` are mandatory on add

That last one is not fussiness. `calibredb add` with no metadata parses the
FILENAME as "Title - Author", while most sources name files "Author - Title",
so the record silently lands inverted and is only noticed later as a backwards
folder path.

Writes shell out to `calibredb`. They never touch metadata.db directly: Calibre
maintains derived state (path layout, search caches, link tables) that raw SQL
desynchronises.

DUPLICATE DETECTION AND THE ONEDRIVE PROBLEM
--------------------------------------------
The library lives on OneDrive and most files are dataless placeholders: reading
one byte hydrates the whole file, so hashing the library is a multi-GB download.

So the cheap signals run first, straight out of the catalogue:

  1. size, from the `data.uncompressed_size` column  -- zero file reads
  2. ISBN, from `identifiers`                        -- zero file reads
  3. normalised title + first-author surname         -- zero file reads

Only a *size collision* justifies hashing, and then only the one or two
colliding files. In the common case this costs no I/O at all.

Caveat worth knowing: `uncompressed_size` can be stale where a format was
reprocessed in place, so a size match is a candidate rather than a verdict --
which is exactly why it triggers a hash instead of a block.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time

from calibre_core.duplicates import dupok_pairs, excused_within, sha256
from calibre_core.isbn import clean_isbn
from calibre_core.library import connect, db_path, library_path
from calibre_core.normalize import author_surname, dedup_key


class WriteBlocked(Exception):
    """A gate refused the operation. Carries a machine-readable reason."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


# Searched in order when `calibredb` is not on PATH. This used to be the single
# hardcoded string "/opt/homebrew/bin/calibredb" -- an assumption about one
# machine's package manager, which fails silently-ish anywhere else.
_CALIBREDB_FALLBACKS = (
    "/opt/homebrew/bin/calibredb",
    "/usr/local/bin/calibredb",
    "/Applications/calibre.app/Contents/MacOS/calibredb",
)


def calibredb_path() -> str:
    """Locate the `calibredb` binary, PATH first.

    Resolved at CALL time, not import: importing this module on a machine with no
    Calibre installed must not explode, and a test must be able to run every gate
    that fires before the binary is ever needed.
    """
    found = shutil.which("calibredb")
    if found:
        return found
    for cand in _CALIBREDB_FALLBACKS:
        if os.path.exists(cand):
            return cand
    raise WriteBlocked(
        "calibredb not found on PATH or in the known install locations",
        {"searched": ["$PATH", *_CALIBREDB_FALLBACKS]},
    )


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def gui_is_open() -> bool:
    """True if the Calibre GUI is running. calibredb must not write while it is."""
    return (
        subprocess.run(["pgrep", "-x", "calibre"], capture_output=True, check=False).returncode
        == 0
    )


def backup_db(dest_dir: str) -> str:
    """Copy metadata.db aside, returning the backup path.

    A rollback reverts the catalogue but NOT the filesystem, so any book added
    after the backup becomes an orphan -- a directory with no DB row, invisible
    to the GUI and to `calibredb list`. Run an orphan scan after restoring one.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"metadata.db.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(str(db_path()), dest)
    return dest


# --------------------------------------------------------------------------
# duplicate detection
# --------------------------------------------------------------------------

# `dedup_key`, `author_surname`, `sha256` and `dupok_pairs` come from
# calibre_core rather than being defined here. Each local copy they replaced had
# a defect the shared version does not:
#
#   dedup_key       -- destroyed Korean and kana voicing marks
#   _surname        -- folded no accents and stripped no punctuation, so
#                      'Duran' and 'Heuer Jr.' keyed wrongly
#   _dupok_partners -- stored the raw column value then filtered
#                      `if x.isdigit()`, so a #dupok of "954,995" failed the
#                      digit test as a whole string and was silently dropped:
#                      multi-partner exemptions had NEVER worked.
#
# Keeping them here is what let three copies of one normaliser drift apart in
# three repos inside a single afternoon.
#
# The three signals below are one function each -- not inlined into
# `check_duplicate` -- because they are independent and answer different
# questions, and because a single 90-line function tripped ruff's C901. Each keeps
# the comment explaining what its predecessor got wrong; the ORDER they are called
# in is the cheapest-first order the docstring promises.


def _isbn_signals(con: sqlite3.Connection, isbn: str | None) -> list[dict]:
    """Exact, free, and decisive.

    `clean_isbn` runs isbnlib.canonical, which REJECTS malformed lengths. The
    `re.sub(r"[^0-9Xx]","")` it replaced kept anything, so a 12-digit typo could
    match another 12-digit typo -- garbage matching garbage on a signal whose
    verdict is `block`.
    """
    if not isbn or not (want := clean_isbn(isbn)):
        return []
    return [
        {"signal": "isbn", "book_id": bid, "detail": f"ISBN {val}"}
        for bid, val in con.execute(
            "SELECT book, val FROM identifiers WHERE type='isbn'"
        )
        if clean_isbn(val) == want
    ]


def _size_signals(rows: list[tuple], staged_path: str | None) -> list[dict]:
    """Size comes from the catalogue, so no file is read to find a candidate."""
    if not staged_path or not os.path.exists(staged_path):
        return []
    staged_size = os.path.getsize(staged_path)
    signals: list[dict] = []
    for r in [r for r in rows if r[6] and int(r[6]) == staged_size]:
        bid, btitle, bpath, dname, dfmt = r[0], r[1], r[3], r[4], r[5]
        # A size collision is only a candidate -- confirm by hashing, and hash
        # ONLY this file so OneDrive hydrates one book, not 1000.
        cand = os.path.join(str(library_path()), bpath, f"{dname}.{dfmt.lower()}")
        same_hash = None
        if os.path.exists(cand):
            try:
                same_hash = sha256(cand) == sha256(staged_path)
            except OSError:
                same_hash = None
        signals.append(
            {
                "signal": "sha256" if same_hash else "size",
                "book_id": bid,
                "detail": (
                    f"identical file ({staged_size:,} bytes) as {btitle!r}"
                    if same_hash
                    else f"same size ({staged_size:,} bytes) as {btitle!r}"
                    + ("" if same_hash is False else "; hash inconclusive")
                ),
            }
        )
    return signals


def _title_author_signals(
    rows: list[tuple], title: str | None, authors: str | None
) -> list[dict]:
    """Normalised title + surname: cheap, and needs a human."""
    if not title:
        return []
    k, sn = dedup_key(title), author_surname(authors or "")
    signals: list[dict] = []
    seen: set[int] = set()
    for r in rows:
        bid, btitle, bauth = r[0], r[1], r[2]
        if bid in seen:
            continue
        if dedup_key(btitle) == k and k and author_surname(bauth) == sn:
            seen.add(bid)
            signals.append(
                {"signal": "title+author", "book_id": bid,
                 "detail": f"{btitle!r} — {bauth}"}
            )
    return signals


def check_duplicate(
    staged_path: str | None = None,
    title: str | None = None,
    authors: str | None = None,
    isbn: str | None = None,
) -> dict:
    """Look for an existing record matching the thing about to be added.

    Returns {"verdict": "block" | "warn" | "ok", "signals": [...]}.

    block -- no judgement required: the same file, or the same edition.
             identical sha256; identical size AND page count; same ISBN.
    warn  -- plausible but needs a human: same normalised title + author
             surname. Suppressed when #dupok already pairs the two records.
    """
    con = connect()
    try:
        # Two queries, not one join: mixing the authors fan-out with the formats
        # fan-out multiplies rows, and GROUP_CONCAT(DISTINCT x, sep) is a syntax
        # error in SQLite (DISTINCT aggregates take exactly one argument).
        meta = {
            bid: (title, auth or "", path)
            for bid, title, auth, path in con.execute(
                """
                SELECT b.id, b.title,
                       (SELECT GROUP_CONCAT(a.name, ' & ')
                          FROM books_authors_link al JOIN authors a ON a.id = al.author
                         WHERE al.book = b.id),
                       b.path
                FROM books b
                """
            )
        }
        rows = [
            (bid, *meta.get(bid, ("", "", "")), dname, dfmt, usize)
            for bid, dname, dfmt, usize in con.execute(
                "SELECT book, name, format, uncompressed_size FROM data"
            )
            if bid in meta
        ]

        # Cheapest first, and in this order: ISBN (a catalogue lookup), size (one
        # stat, hashing only a collision), title+author (in-memory normalisation).
        signals: list[dict] = [
            *_isbn_signals(con, isbn),
            *_size_signals(rows, staged_path),
            *_title_author_signals(rows, title, authors),
        ]

        # ---- #dupok suppression, applied to warn-level signals only ------
        pairs = dupok_pairs()
    finally:
        con.close()

    # Suppress a title+author warning only when the matched record is paired with
    # ANOTHER record that also matched. The old code flattened every partner into
    # one set and suppressed on mere membership, so if an unrelated record named
    # two books as its partners, a genuine duplicate between those two reported
    # `ok`. Symmetric and fails open, so a human annotates only one side.
    matched = {s["book_id"] for s in signals if s["signal"] == "title+author"}

    hard = [s for s in signals if s["signal"] in ("sha256", "isbn")]
    soft = [s for s in signals if s["signal"] == "title+author"
            and not excused_within(s["book_id"], matched, pairs)]
    maybe = [s for s in signals if s["signal"] == "size"]

    verdict = "block" if hard else ("warn" if soft or maybe else "ok")
    return {"verdict": verdict, "signals": signals}


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

def _run(args: list[str]) -> str:
    r = subprocess.run(
        [calibredb_path(), *args, "--with-library", str(library_path())],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise WriteBlocked(f"calibredb failed: {(r.stderr or '').strip()[:400]}")
    return (r.stdout or "").strip()


def add_book(
    path: str,
    title: str,
    authors: str,
    tags: str = "",
    isbn: str = "",
    backup_dir: str = "/tmp/calibre-db-backups",
    force: bool = False,
) -> dict:
    """Add a staged file, refusing on a guaranteed duplicate.

    title and authors are REQUIRED -- see the module docstring for why omitting
    them silently inverts the record.

    force=True downgrades a `block` to a warning. Use it only for a deliberate
    quality pair (born-digital plus scan, or two editions), and mark the pair
    with #dupok afterwards so the next run does not stop again.
    """
    if not os.path.exists(path):
        raise WriteBlocked("staged file does not exist", {"path": path})
    if not os.path.splitext(path)[1].lstrip("."):
        # calibredb infers the format from the extension. Given none it exits 0,
        # prints nothing, and adds nothing -- a success-shaped no-op.
        raise WriteBlocked(
            "staged file has no extension — calibredb infers the format from it "
            "and silently adds nothing without one",
            {"path": path},
        )
    if not title or not authors:
        raise WriteBlocked("title and authors are mandatory (filename parsing inverts records)")
    if gui_is_open():
        raise WriteBlocked("Calibre GUI is open — close it before writing")

    dup = check_duplicate(staged_path=path, title=title, authors=authors, isbn=isbn)
    if dup["verdict"] == "block" and not force:
        raise WriteBlocked("guaranteed duplicate — refusing to add", dup)

    backup = backup_db(backup_dir)
    args = ["add", path, "--title", title, "--authors", authors]
    if tags:
        args += ["--tags", tags]
    if isbn:
        args += ["--identifier", f"isbn:{isbn}"]
    out = _run(args)
    new_ids = (
        [int(n) for n in re.findall(r"\b(\d+)\b", out.split("ids:")[-1])]
        if "ids:" in out
        else []
    )
    if not new_ids:
        # calibredb can exit 0 having done nothing. Reporting ok=True here would
        # make a no-op indistinguishable from an add.
        raise WriteBlocked(
            "calibredb exited 0 but reported no new book id — nothing was added",
            {"path": path, "calibredb_output": out, "db_backup": backup},
        )
    return {"ok": True, "calibredb": out, "book_ids": new_ids,
            "duplicate_check": dup, "db_backup": backup}


def current_identifiers(book_id: int) -> dict[str, str]:
    """The record's identifiers as {type: value}. Read-only."""
    con = connect()
    try:
        return {
            t: v for t, v in con.execute(
                "SELECT type, val FROM identifiers WHERE book=?", (book_id,)
            )
        }
    finally:
        con.close()


def _merge_identifiers(book_id: int, incoming: str) -> str:
    """Fold `incoming` over the record's existing identifiers.

    `calibredb set_metadata --field identifiers:...` REPLACES the whole set, it
    does not merge. Setting an ISBN on book 256 that way silently deleted its
    `zotero` identifier -- and that identifier is the link to the Zotero facade
    item, i.e. the book's citations and `zotero://` deep links. Nothing in the
    output announced the loss; it showed up only as a shorter Identifiers line.

    Same value for a type overwrites; every other type is carried through. To
    REMOVE an identifier, pass the full desired set explicitly -- that is the one
    operation this cannot express, which is deliberate: dropping a key should be
    a decision, not a side effect of setting a different one.
    """
    merged = current_identifiers(book_id)
    for pair in (incoming or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        t, _, v = pair.partition(":")
        merged[t.strip()] = v.strip()
    return ",".join(f"{t}:{v}" for t, v in sorted(merged.items()))


def remove_identifier(
    book_id: int,
    id_type: str,
    backup_dir: str = "/tmp/calibre-db-backups",
) -> dict:
    """Drop ONE identifier type from a record, keeping the rest.

    Deliberately a separate function rather than a flag on set_book_metadata:
    that path merges, so it cannot express removal, and making removal reachable
    by accident is how book 256 lost its `zotero` link. Here the caller has to
    name the type they mean.

    Used for a WRONG identifier, which is worse than a missing one -- books 49 and
    1057 both carried isbn 4412957810, whose group prefix is Japan while both
    books are British, so the ISBN signal reported them as a guaranteed duplicate
    of each other and refused re-adds.
    """
    if gui_is_open():
        raise WriteBlocked("Calibre GUI is open — close it before writing")
    before = current_identifiers(book_id)
    if id_type not in before:
        raise WriteBlocked(
            f"book {book_id} has no {id_type!r} identifier — refusing a no-op write",
            {"present": sorted(before)},
        )
    kept = {t: v for t, v in before.items() if t != id_type}
    backup = backup_db(backup_dir)
    value = ",".join(f"{t}:{v}" for t, v in sorted(kept.items()))
    out = _run(["set_metadata", str(book_id), "--field", f"identifiers:{value}"])
    after = current_identifiers(book_id)
    if id_type in after:
        raise WriteBlocked(
            f"{id_type!r} survived the write on book {book_id}",
            {"before": before, "after": after, "db_backup": backup},
        )
    return {"ok": True, "calibredb": out, "removed": {id_type: before[id_type]},
            "before": before, "after": after, "db_backup": backup}


def set_book_metadata(
    book_id: int,
    fields: dict[str, str],
    backup_dir: str = "/tmp/calibre-db-backups",
) -> dict:
    """Set metadata fields on one record via `calibredb set_metadata --field`.

    Targeted per-field writes only -- pushing a whole OPF overwrites local tags,
    which are knowledge this library keeps and no online source has.

    Refuses to touch `title` or `authors`: both rename the on-disk directory, and
    since a metadata.db rollback does not revert the filesystem there is no undo.
    Do those in the Calibre GUI, which moves files transactionally.

    `identifiers` is MERGED over what the record already has -- see
    `_merge_identifiers` for why a plain write is destructive.
    """
    if gui_is_open():
        raise WriteBlocked("Calibre GUI is open — close it before writing")
    protected = {"title", "authors"} & {k.lower() for k in fields}
    if protected:
        raise WriteBlocked(
            f"refusing to set {sorted(protected)} — a rename moves the directory "
            "and no rollback exists; use the Calibre GUI",
            {"fields": sorted(protected)},
        )
    fields = dict(fields)
    merged_note = None
    for key in [k for k in fields if k.lower() == "identifiers"]:
        before = current_identifiers(book_id)
        fields[key] = _merge_identifiers(book_id, fields[key])
        merged_note = {"before": before, "written": fields[key]}

    backup = backup_db(backup_dir)
    args = ["set_metadata", str(book_id)]
    for k, v in fields.items():
        args += ["--field", f"{k}:{v}"]
    out = {"ok": True, "calibredb": _run(args), "db_backup": backup}
    if merged_note:
        out["identifiers_merged"] = merged_note
    return out
