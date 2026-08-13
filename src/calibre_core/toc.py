"""Writing a table of contents INTO a library PDF.

Reading an outline is a PDF operation and does not belong here — omni-rag's
parsers do it during ingest, on staged copies, on hosts that have no Calibre
library at all. What belongs here is the *write*: `set_toc` on a file inside
`Author/Title (id)/` mutates a file Calibre owns, and that is this package's
business for the same reason `writes.py` is.

This started life as `_inject` in omni-rag's `scripts/inject_toc.py`, where it
replaced library PDFs **with no GUI gate at all**. Every other write in this
package refuses while the Calibre GUI is open; this one did not, so a batch run
during an open Calibre could swap a file out from under the viewer and the
metadata cache. The gate is the reason the function moved, not a bonus.

WHY THE GATE IS RAISED AND EVERYTHING ELSE IS RETURNED
-----------------------------------------------------
`inject_outline` reports two different KINDS of outcome, so it uses two
mechanisms:

  * `WriteBlocked` — the GUI is open. That is a *batch* precondition: it is
    equally true for every remaining file, so retrying the next one is pointless
    and a caller looping over 700 books wants to stop.
  * `{"ok": False, "reason": ...}` — this one file was left alone (no entries
    survived sanitising, verification failed, it already has an outline). That is
    per-file data the caller aggregates and keeps going.

Collapsing the two would mean either a loop that dies on one bad PDF or a batch
that silently writes nothing 700 times while Calibre is open.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from calibre_core.writes import WriteBlocked, gui_is_open


def sanitize_outline(entries: list[dict], page_count: int) -> list[list]:
    """Coerce agent- or parser-supplied entries into what `set_toc` accepts.

    PyMuPDF enforces three rules and raises on violation: the first entry is
    level 1, a level may only step UP by one at a time, and every page target is
    in range. Real extractions break all three — a printed TOC that starts at a
    sub-heading, a jump from level 1 to level 3 where a middle heading was
    missed, and page targets computed from a printed folio with the wrong offset.

    Entries with no title or no integer `pdf_page` are dropped rather than
    repaired: a bookmark with no text is unclickable, and a guessed page is worse
    than a missing one.
    """
    out: list[list] = []
    prev = 0
    for e in entries:
        title = " ".join(str(e.get("title", "")).split())
        page = e.get("pdf_page")
        if not title or not isinstance(page, int):
            continue
        level = max(1, int(e.get("level", 1)))
        level = 1 if not out else min(level, prev + 1)
        out.append([level, title, max(1, min(page, page_count))])
        prev = level
    return out


def has_outline(path: str | Path) -> bool:
    """True if the file carries an outline with at least one titled entry.

    The titled-entry test is not pedantry. Some PDFs ship an outline whose every
    title is empty — structurally present, useless to a reader, and invisible to
    an extractor, which is how such a file lands in an injection work list in the
    first place. Treating it as "has an outline" would make those books
    permanently unfixable.
    """
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return any(str(t[1]).strip() for t in doc.get_toc(simple=True))


def inject_outline(
    path: str | Path,
    entries: list[dict],
    backup_dir: str | Path,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Write `entries` as the PDF's outline, atomically, keeping a backup.

    Returns `{"ok": True, "entries": n}` on success, or
    `{"ok": False, "reason": str}` when this file was deliberately left
    untouched. Raises `WriteBlocked` only when the Calibre GUI is open — see the
    module docstring for why those are different.

    The sequence is save-elsewhere, verify, then replace, because the failure
    being defended against is a *successful-looking* save that damages the file.
    `set_toc` rewrites the document structure, so the saved copy is checked for
    the page count, the outline length, and unchanged text on sampled pages
    before it is allowed to become the original. A copy that fails any of those,
    or cannot be reopened at all, is deleted and the original never moves.
    """
    import pymupdf

    path = Path(path)
    if gui_is_open():
        raise WriteBlocked(
            "Calibre GUI is open — close it before rewriting library files",
            {"path": str(path)},
        )
    if not path.exists():
        return {"ok": False, "reason": "file missing"}

    with pymupdf.open(str(path)) as doc:
        if not replace_existing and any(str(t[1]).strip() for t in doc.get_toc(simple=True)):
            return {"ok": False, "reason": "already has an outline (previous run?)"}
        toc = sanitize_outline(entries, doc.page_count)
        if not toc:
            return {"ok": False, "reason": "no injectable entries after sanitizing"}
        sample = [0, doc.page_count // 2]
        before = [doc[i].get_text() for i in sample]
        doc.set_toc(toc)
        tmp = path.with_suffix(".tocinject.pdf")
        doc.save(str(tmp))

    try:
        with pymupdf.open(str(tmp)) as chk:
            ok = (
                chk.page_count > 0
                and len(chk.get_toc(simple=True)) == len(toc)
                and [chk[i].get_text() for i in sample] == before
            )
        if not ok:
            tmp.unlink(missing_ok=True)
            return {
                "ok": False,
                "reason": "verification failed on saved copy "
                "(page count / outline length / sampled text mismatch)",
            }
    except Exception as exc:  # noqa: BLE001 - a broken save must never replace the original
        tmp.unlink(missing_ok=True)
        return {"ok": False, "reason": f"saved copy unreadable: {exc}"}

    # The backup is taken only now, once the replacement is known good: copying
    # first would leave a backup behind for every file that turned out not to
    # need one.
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / path.name
    shutil.copy2(path, backup)
    os.replace(tmp, path)
    return {"ok": True, "entries": len(toc), "backup": str(backup)}
