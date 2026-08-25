"""Use-case orchestration for Calibre writes.

Each function here composes domain rules (duplicate detection, validation) with
infrastructure (calibredb subprocess, DB backup, identifier queries) into one
gated operation. No subprocess calls or filesystem access of its own.
"""

from __future__ import annotations

import os
import re
import sqlite3

from calibre_core.domain.duplicates import dupok_pairs, excused_within, sha256
from calibre_core.domain.isbn import clean_isbn
from calibre_core.domain.normalize import author_surname, dedup_key
from calibre_core.domain.write_rules import WriteBlocked, reject_html_entities
from calibre_core.infrastructure.calibredb import (
    backup_db,
    calibredb_path,
    current_identifiers,
    gui_is_open,
    merge_identifiers,
    run_calibredb,
)
from calibre_core.infrastructure.sqlite import connect, library_path


# --------------------------------------------------------------------------
# duplicate detection
# --------------------------------------------------------------------------

def _isbn_signals(con: sqlite3.Connection, isbn: str | None) -> list[dict]:
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
    if not staged_path or not os.path.exists(staged_path):
        return []
    staged_size = os.path.getsize(staged_path)
    signals: list[dict] = []
    for r in [r for r in rows if r[6] and int(r[6]) == staged_size]:
        bid, btitle, bpath, dname, dfmt = r[0], r[1], r[3], r[4], r[5]
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
    """Look for an existing record matching the thing about to be added."""
    con = connect()
    try:
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
        signals: list[dict] = [
            *_isbn_signals(con, isbn),
            *_size_signals(rows, staged_path),
            *_title_author_signals(rows, title, authors),
        ]
        pairs = dupok_pairs()
    finally:
        con.close()

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

def add_book(
    path: str,
    title: str,
    authors: str,
    tags: str = "",
    isbn: str = "",
    backup_dir: str = "/tmp/calibre-db-backups",
    force: bool = False,
) -> dict:
    """Add a staged file, refusing on a guaranteed duplicate."""
    if not os.path.exists(path):
        raise WriteBlocked("staged file does not exist", {"path": path})
    if not os.path.splitext(path)[1].lstrip("."):
        raise WriteBlocked(
            "staged file has no extension — calibredb infers the format from it "
            "and silently adds nothing without one",
            {"path": path},
        )
    if not title or not authors:
        raise WriteBlocked("title and authors are mandatory (filename parsing inverts records)")
    reject_html_entities(title=title, authors=authors, tags=tags)

    dup = check_duplicate(staged_path=path, title=title, authors=authors, isbn=isbn)
    if dup["verdict"] == "block" and not force:
        raise WriteBlocked("guaranteed duplicate — refusing to add", dup)

    backup = backup_db(backup_dir)
    args = ["add", path, "--title", title, "--authors", authors]
    if tags:
        args += ["--tags", tags]
    if isbn:
        args += ["--identifier", f"isbn:{isbn}"]
    out = run_calibredb(args)
    new_ids = (
        [int(n) for n in re.findall(r"\b(\d+)\b", out.split("ids:")[-1])]
        if "ids:" in out
        else []
    )
    if not new_ids:
        raise WriteBlocked(
            "calibredb exited 0 but reported no new book id — nothing was added",
            {"path": path, "calibredb_output": out, "db_backup": backup},
        )
    return {"ok": True, "calibredb": out, "book_ids": new_ids,
            "duplicate_check": dup, "db_backup": backup}


def add_format(
    book_id: int,
    path: str,
    backup_dir: str = "/tmp/calibre-db-backups",
    force: bool = False,
) -> dict:
    """Attach an additional format to an EXISTING record."""
    if not os.path.exists(path):
        raise WriteBlocked("staged file does not exist", {"path": path})
    ext = os.path.splitext(path)[1].lstrip(".").upper()
    if not ext:
        raise WriteBlocked(
            "staged file has no extension — calibredb infers the format from it",
            {"path": path},
        )
    con = connect()
    try:
        row = con.execute("SELECT title FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise WriteBlocked(f"no book with id {book_id}", {"book_id": book_id})
        existing = [r[0] for r in con.execute("SELECT format FROM data WHERE book = ?", (book_id,))]
    finally:
        con.close()
    if ext in existing and not force:
        raise WriteBlocked(
            f"record {book_id} already has a {ext} — calibredb would REPLACE it with no undo; "
            f"pass force=True only if losing the current {ext} is intended",
            {"book_id": book_id, "format": ext, "existing": existing, "hint": "force=True"},
        )

    backup = backup_db(backup_dir)
    out = run_calibredb(["add_format", str(book_id), path])
    con = connect()
    try:
        after = [r[0] for r in con.execute("SELECT format FROM data WHERE book = ?", (book_id,))]
    finally:
        con.close()
    if ext not in after:
        raise WriteBlocked(
            f"calibredb exited 0 but record {book_id} still has no {ext}",
            {"book_id": book_id, "formats": after, "calibredb": out},
        )
    return {
        "ok": True,
        "book_id": book_id,
        "title": row[0],
        "added": ext,
        "formats": after,
        "replaced": ext in existing,
        "calibredb": out,
        "db_backup": backup,
    }


def remove_identifier(
    book_id: int,
    id_type: str,
    backup_dir: str = "/tmp/calibre-db-backups",
) -> dict:
    """Drop ONE identifier type from a record, keeping the rest."""
    before = current_identifiers(book_id)
    if id_type not in before:
        raise WriteBlocked(
            f"book {book_id} has no {id_type!r} identifier — refusing a no-op write",
            {"present": sorted(before)},
        )
    kept = {t: v for t, v in before.items() if t != id_type}
    backup = backup_db(backup_dir)
    value = ",".join(f"{t}:{v}" for t, v in sorted(kept.items()))
    out = run_calibredb(["set_metadata", str(book_id), "--field", f"identifiers:{value}"])
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
    force: bool = False,
) -> dict:
    """Set metadata fields on one record via `calibredb set_metadata --field`."""
    reject_html_entities(**fields)

    lowered = {k.lower() for k in fields}
    if "authors" in lowered:
        raise WriteBlocked(
            "refusing to set 'authors' — that moves the book to a different author "
            "directory, not just a rename; use the Calibre GUI",
            {"fields": ["authors"]},
        )
    if "title" in lowered and not force:
        raise WriteBlocked(
            "setting 'title' renames the on-disk directory — pass force=True once you "
            "have read what goes stale (see this function's docstring)",
            {"fields": ["title"], "hint": "force=True"},
        )
    fields = dict(fields)
    merged_note = None
    for key in [k for k in fields if k.lower() == "identifiers"]:
        before = current_identifiers(book_id)
        fields[key] = merge_identifiers(book_id, fields[key])
        merged_note = {"before": before, "written": fields[key]}

    backup = backup_db(backup_dir)
    args = ["set_metadata", str(book_id)]
    for k, v in fields.items():
        args += ["--field", f"{k}:{v}"]
    out = {"ok": True, "calibredb": run_calibredb(args), "db_backup": backup}
    if merged_note:
        out["identifiers_merged"] = merged_note
    return out
