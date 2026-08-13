"""The command line — for consumers that cannot import this package at all.

Every other consumer imports `calibre_core` (the MCP server, `calfuzz`, omni-rag's
scripts). calibre-page-inserter cannot: it is an Obsidian plugin, Obsidian spawns
its helper with a bare `python3` interpreter with no venv, and installing
calibre-core into the system interpreter is not a thing to do. A subprocess has
exactly one way to reach a library — a command — so this is that command.

It holds NO Calibre logic, which is the same rule every other interface follows:
argparse in, a public calibre_core call, `json.dumps` out. If a subcommand ever
needs to decide something about a library, that decision belongs in a module
next door and this file calls it.

Conventions follow `calfuzz` (calibre-mcp's CLI): `--json` for machine-readable
output on stdout, a human-readable table otherwise, and failures as a message on
stderr with a non-zero exit — 2 for "no library there", 1 for anything else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from calibre_core.library import LibraryNotFound, SchemaError, library_path
from calibre_core.paths import resolve_path
from calibre_core.records import Book, load_books


def _format_codes(book: Book) -> list[str]:
    """Format CODES ("PDF", "EPUB") — what a caller building a calibre:// URL needs.

    `Book.formats` carries absolute paths and stays that way (a path reduces to
    its code, a code cannot be turned back into a path), so the reduction happens
    here at the edge. It is exact rather than a guess: Calibre names every format
    file `<name>.<format.lower()>`, so the suffix IS the `data.format` value.
    """
    return sorted({p.suffix.lstrip(".").upper() for p in book.formats})


def _record(book: Book) -> dict:
    """The wire shape. `authors` is the ampersand-joined string, matching
    `Book.authors_str` and the house convention, not a list — every consumer of
    this field renders or splits it as one string."""
    return {
        "id": book.id,
        "uuid": book.uuid,
        "title": book.title,
        "authors": book.authors_str,
        "formats": _format_codes(book),
    }


def cmd_books(_args: argparse.Namespace, as_json: bool) -> int:
    # Sorted by title here rather than in SQL: ordering is presentation, and this
    # is the layer that presents. Ties broken by id so the output is stable.
    books = sorted(load_books(), key=lambda b: (b.title.casefold(), b.id))
    rows = [_record(b) for b in books]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['id']:>5}  {r['title'][:60]:<60}  {'/'.join(r['formats']) or '-':<10}"
                  f"  {r['authors'][:40]}")
    return 0


def cmd_resolve(args: argparse.Namespace, as_json: bool) -> int:
    """Each path -> the record whose book folder holds it, or null.

    A list aligned with the arguments as given, each entry carrying its own
    `path`, so a caller that passed an ordered batch (Skim's window stacking
    order) can pair the answers back without re-deriving anything.
    """
    out = []
    for p in args.path:
        book = resolve_path(p)
        out.append({"path": p, "book": _record(book) if book else None})
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for entry in out:
            book = entry["book"]
            print(f"{book['id']:>5}  {book['title']}" if book else "    -  (not in library)")
    return 0


def main(argv: list[str] | None = None) -> int:
    # `--library` and `--json` are attached to BOTH the top level and every
    # subparser so either order works. They must default to SUPPRESS to do that:
    # a subparser writes its own defaults into the same namespace, so a plain
    # `default=False` on the child would silently overwrite the `--json` the user
    # typed before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--library",
        default=argparse.SUPPRESS,
        help="library root holding metadata.db (default: $CALIBRE_LIBRARY, else ~/Calibre Library)",
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-readable JSON on stdout",
    )

    ap = argparse.ArgumentParser(
        prog="calibre-core",
        parents=[common],
        description="Read-only queries against the Calibre catalogue.",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("books", parents=[common], help="every book: id, uuid, title, authors, formats")
    p_resolve = sub.add_parser(
        "resolve", parents=[common], help="filesystem path(s) -> the matching record, or null"
    )
    p_resolve.add_argument("path", nargs="+")

    args = ap.parse_args(argv)
    as_json = getattr(args, "json", False)
    library = getattr(args, "library", None)
    if library:
        # `library_path()` reads the environment at CALL time — that is the
        # package's injection seam, and setting it here redirects the catalogue
        # AND the root that format paths hang off in one move.
        os.environ["CALIBRE_LIBRARY"] = str(Path(library).expanduser())

    handler = {"books": cmd_books, "resolve": cmd_resolve}[args.command]
    try:
        return handler(args, as_json)
    except BrokenPipeError:
        # `calibre-core books | head` closes the pipe mid-write. Nothing is wrong —
        # the reader left — but Python prints a traceback AND a second "Exception
        # ignored" when it flushes stdout at shutdown. Pointing stdout at devnull
        # is the documented way to keep that final flush quiet.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except LibraryNotFound as e:
        print(f"{e} (library: {library_path()})", file=sys.stderr)
        return 2
    except SchemaError as e:
        print(f"unexpected Calibre schema: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
