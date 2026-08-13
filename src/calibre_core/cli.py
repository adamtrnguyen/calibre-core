"""The command line — for import-less consumers, and for tools you run.

Two different populations use this, and both matter:

  * Consumers that CANNOT import the package. calibre-page-inserter is an
    Obsidian plugin, Obsidian spawns its helper with a bare `python3` and no
    venv, and installing calibre-core into the system interpreter is not a thing
    to do. A subprocess has exactly one way to reach a library — a command.
  * Tools a person runs. `audit` and `metadata-candidates` arrived here from
    omni-rag's `scripts/`, where they were loose files you had to remember the
    path of. A thing you run is a command; that is the whole reason they moved.

`toc.inject_outline` is deliberately NOT here. It is called by a batch job that
imports this package, so a subcommand would be surface with no consumer — add
one when something that cannot import shows up needing it.

This file holds NO Calibre logic, which is the same rule every other interface
follows: argparse in, a public calibre_core call, `json.dumps` out. If a
subcommand ever needs to decide something about a library, that decision belongs
in a module next door and this file calls it.

Conventions follow `calfuzz` (calibre-mcp's CLI): `--json` for machine-readable
output on stdout, a human-readable table otherwise, and failures as a message on
stderr with a non-zero exit — 2 for "no library there", 1 for anything else.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from calibre_core import audit, openlibrary
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


def _stamped(out_dir: Path, stem: str) -> tuple[Path, Path]:
    """`(json, markdown)` paths under a timestamped name.

    Reports are never overwritten: the value of an audit is comparing it to the
    last one, and a fixed filename destroys the thing you wanted.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return out_dir / f"{stem}-{stamp}.json", out_dir / f"{stem}-{stamp}.md"


def cmd_audit(args: argparse.Namespace, as_json: bool) -> int:
    report = audit.audit(
        library_path(),
        scan_missing=args.scan_missing_isbns,
        pages_each_end=args.pages_each_end,
        scan_timeout=args.scan_timeout,
        limit_scan=args.limit_scan,
        progress_every=args.progress_every,
        scan_workers=args.scan_workers,
    )
    json_path, md_path = _stamped(args.out_dir, "calibre-metadata-audit")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    audit.write_markdown(report, md_path)
    # The summary goes to stdout either way -- `--json` controls the SHAPE, and a
    # report you cannot see the result of is a report you rerun.
    result = {"json": str(json_path), "markdown": str(md_path), **report["summary"]}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_metadata_candidates(args: argparse.Namespace, as_json: bool) -> int:
    report = openlibrary.build(
        json.loads(args.audit_json.read_text()),
        timeout=args.timeout,
        workers=args.workers,
        limit=args.limit,
        include_multi=args.include_multi,
        # Progress to stderr, not stdout: stdout carries the result, and a caller
        # doing `... --json | jq` must not be fed counters.
        on_progress=lambda i, n: print(f"lookup {i}/{n}", file=sys.stderr, flush=True),
    )
    json_path, md_path = _stamped(args.out_dir, "calibre-openlibrary-candidates")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    openlibrary.write_markdown(report, md_path)
    result = {"json": str(json_path), "markdown": str(md_path), **report["summary"]}
    print(json.dumps(result, ensure_ascii=False) if as_json else json.dumps(result, indent=2))
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

    p_audit = sub.add_parser(
        "audit",
        parents=[common],
        help="metadata-cleanup candidates: thin records + duplicate groups",
    )
    p_audit.add_argument("--out-dir", type=Path, default=Path("reports"))
    p_audit.add_argument(
        "--scan-missing-isbns",
        action="store_true",
        help="also scan PDF front/back matter for printed ISBNs (needs the 'pdf' extra)",
    )
    p_audit.add_argument("--pages-each-end", type=int, default=8)
    p_audit.add_argument(
        "--scan-timeout", type=int, default=20, help="seconds before one PDF scan is killed"
    )
    p_audit.add_argument(
        "--limit-scan", type=int, default=0, help="scan only the first N eligible PDFs"
    )
    p_audit.add_argument(
        "--progress-every", type=int, default=25, help="0 to silence scan progress"
    )
    p_audit.add_argument(
        "--scan-workers", type=int, default=4, help="parallel PDF scan subprocesses"
    )

    p_cand = sub.add_parser(
        "metadata-candidates",
        parents=[common],
        help="look an audit's ISBNs up on Open Library and score the match",
    )
    p_cand.add_argument("audit_json", type=Path, help="the JSON written by `calibre-core audit`")
    p_cand.add_argument("--out-dir", type=Path, default=Path("reports"))
    p_cand.add_argument("--timeout", type=int, default=12)
    p_cand.add_argument("--workers", type=int, default=4)
    p_cand.add_argument("--limit", type=int, default=0)
    p_cand.add_argument(
        "--include-multi",
        action="store_true",
        help="also take the FIRST ISBN from pages listing several (a guess — labelled as one)",
    )

    args = ap.parse_args(argv)
    as_json = getattr(args, "json", False)
    library = getattr(args, "library", None)
    if library:
        # `library_path()` reads the environment at CALL time — that is the
        # package's injection seam, and setting it here redirects the catalogue
        # AND the root that format paths hang off in one move.
        os.environ["CALIBRE_LIBRARY"] = str(Path(library).expanduser())

    handler = {
        "books": cmd_books,
        "resolve": cmd_resolve,
        "audit": cmd_audit,
        "metadata-candidates": cmd_metadata_candidates,
    }[args.command]
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
