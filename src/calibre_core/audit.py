"""Read-only metadata audit over the catalogue.

Reports records that are candidates for ISBN-first metadata cleanup: no valid
ISBN, no publisher, no pubdate, a placeholder author, a filename masquerading as
a title, no tags. Writes nothing to Calibre — the output is a queue for a human
or for `openlibrary.py` to enrich.

Moved here from omni-rag's `scripts/`, where it was 425 lines of pure Calibre
logic sitting in a retrieval repo. It imported only `calibre_core` and the
standard library, which is the tell: nothing about it was about retrieval.

`pymupdf` is needed ONLY for `--scan-missing-isbns` and is imported inside the
worker, so the audit proper runs with the package's base dependencies. Install
the `pdf` extra to enable scanning.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import subprocess
import sys
from concurrent import futures
from pathlib import Path
from typing import Any

from calibre_core.duplicates import title_groups
from calibre_core.isbn import clean_isbn, valid_isbn
from calibre_core.records import Book, load_books

UNKNOWN_AUTHORS = {
    "",
    "unknown",
    "unknown author",
    "administrator",
    "administrateur",
    "owner",
    "calibre",
}
JUNK_TITLE_PATTERNS = [
    re.compile(r"^microsoft word\s*-", re.IGNORECASE),
    re.compile(r"\bebok\b", re.IGNORECASE),
    re.compile(r"\.(pdf|epub|mobi|azw3|djvu|rtf)$", re.IGNORECASE),
    re.compile(r"^b0[0-9a-z]{8}\b", re.IGNORECASE),
]
# A labelled ISBN as copyright pages write it. The label is left IN the match on purpose:
# `clean_isbn` already strips an `ISBN`/`ISBN-13:` prefix, so re-scanning each match for a
# "bare" number was not merely redundant -- it LOST ISBNs. On `ISBN-10 0262035618` the inner
# scan ran the label's own `10` into the digit run and the 12-digit result failed the checksum.
ISBN_CANDIDATE = re.compile(
    r"(?:ISBN(?:-1[03])?[\s:]*)(97[89][-\s]?)?(?:\d[-\s]?){9,12}[\dxX]",
    re.IGNORECASE,
)


def record_isbn(book: Book) -> str:
    isbn = clean_isbn(book.isbn)
    return isbn if valid_isbn(isbn) else ""


def missing_pubdate(book: Book) -> bool:
    # Calibre stores "no date" as year 0101, not NULL, so an emptiness test alone
    # reports every undated book as dated.
    value = book.pubdate or ""
    return not value or value.startswith("0101-")


def missing_publisher(book: Book) -> bool:
    return not (book.publisher or "").strip()


def bad_author(book: Book) -> bool:
    authors = book.authors_str.strip()
    return authors.lower() in UNKNOWN_AUTHORS


def junk_title(book: Book) -> bool:
    title = (book.title or "").strip()
    return not title or any(p.search(title) for p in JUNK_TITLE_PATTERNS)


def file_exts(book: Book) -> set[str]:
    return {p.suffix.lower().lstrip(".") for p in book.formats}


def pdf_paths(book: Book) -> list[Path]:
    return [p for p in book.formats if p.suffix.lower() == ".pdf"]


def extract_isbns_from_text(text: str) -> list[str]:
    """Valid ISBNs in `text`, ISBN-13 first — the 13 is the one worth looking up."""
    values = {i for m in ISBN_CANDIDATE.finditer(text) if valid_isbn(i := clean_isbn(m.group(0)))}
    return sorted(values, key=lambda s: (len(s) != 13, s))


def _scan_pdf_text_layer(path: Path, pages_each_end: int) -> dict[str, Any]:
    """Front and back matter of one PDF -> the ISBNs printed in it.

    Runs in a subprocess (see `scan_pdf_for_isbns`), so an exception here is
    reported as data rather than killing the audit.
    """
    try:
        import pymupdf
    except Exception as exc:  # noqa: BLE001 - not just ImportError; see below
        # Broader than ImportError on purpose. A pymupdf install can fail at
        # import for reasons that are not a missing module -- a mismatched or
        # unloadable native library raises from the C extension, and on this
        # machine that is the likelier failure than the package being absent.
        # Either way the answer is the same: report it per file and keep auditing.
        return {"path": str(path), "error": f"pymupdf unavailable: {exc} (install the 'pdf' extra)"}

    try:
        chunks: list[str] = []
        with pymupdf.open(path) as doc:
            count = len(doc)
            indices = list(range(min(pages_each_end, count)))
            start_last = max(0, count - pages_each_end)
            indices.extend(i for i in range(start_last, count) if i not in indices)
            for i in indices:
                chunks.append(doc[i].get_text() or "")
        text = "\n".join(chunks)
        return {"path": str(path), "isbns": extract_isbns_from_text(text), "text_chars": len(text)}
    except Exception as exc:  # noqa: BLE001 - report bad PDFs, keep auditing
        return {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}


def scan_pdf_for_isbns(path: Path, pages_each_end: int, timeout: int) -> dict[str, Any]:
    """Scan one PDF in a child process so a hostile file cannot take the run down.

    The isolation is the point: a malformed page tree can make pymupdf spin
    indefinitely, and there is no in-process timeout for that. A subprocess has
    one, and a crashed worker costs one record.

    Re-entry is `-m calibre_core.audit`, NOT `__file__`. As a script in another
    repo this re-invoked its own path, which stops working the moment the code is
    installed as a package (a wheel, a zipimport) rather than sitting on disk as
    the file that started the process.
    """
    cmd = [
        sys.executable,
        "-m",
        "calibre_core.audit",
        "--scan-one-pdf",
        str(path),
        "--pages-each-end",
        str(pages_each_end),
    ]
    try:
        completed = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"path": str(path), "error": f"timeout after {timeout}s"}

    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = error[-1] if error else f"exit code {completed.returncode}"
        return {"path": str(path), "error": detail[:240]}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"path": str(path), "error": f"invalid worker json: {exc}"}


def run_pdf_scans(
    jobs: list[tuple[Book, Path]],
    pages_each_end: int,
    scan_timeout: int,
    scan_workers: int,
    progress_every: int,
) -> list[dict[str, Any]]:
    """Scan every job concurrently, returning results in the ORDER GIVEN.

    Order is restored deliberately: `as_completed` yields in finish order, so a
    report built straight off it would reshuffle on every run and diff against
    itself. An audit is something you compare to yesterday's.
    """
    if not jobs:
        return []

    scan_workers = max(1, scan_workers)
    results_by_id: dict[int, dict[str, Any]] = {}
    with futures.ThreadPoolExecutor(max_workers=scan_workers) as executor:
        pending = {
            executor.submit(scan_pdf_for_isbns, pdf, pages_each_end, scan_timeout): (book, pdf)
            for book, pdf in jobs
        }
        for completed_count, future in enumerate(futures.as_completed(pending), start=1):
            book, pdf = pending[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - keep the audit moving
                result = {"path": str(pdf), "error": f"{type(exc).__name__}: {exc}"}
            results_by_id[int(book.id)] = {
                "id": book.id,
                "title": book.title,
                "authors": book.authors_str,
                **result,
            }
            if progress_every > 0 and (
                completed_count == 1
                or completed_count == len(jobs)
                or completed_count % progress_every == 0
            ):
                # `book.id`, not `book['id']`. The version this moved from used
                # subscript here and `Book` is a frozen dataclass, so the FIRST
                # progress line raised TypeError -- and this line fires on
                # completed_count == 1, which made `--scan-missing-isbns`
                # unusable at any --progress-every above 0 (default 25).
                title = str(book.title or "").replace("\n", " ")[:90]
                print(
                    f"scan {completed_count}/{len(jobs)}: id={book.id} {title}",
                    file=sys.stderr,
                    flush=True,
                )

    return [results_by_id[int(book.id)] for book, _pdf in jobs if int(book.id) in results_by_id]


def classify(
    books: list[Book],
    library: Path,
    scan_missing: bool = False,
    pages_each_end: int = 8,
    scan_timeout: int = 20,
    limit_scan: int = 0,
    progress_every: int = 25,
    scan_workers: int = 4,
) -> dict[str, Any]:
    """The report, as data. Rendering is `write_markdown`'s job."""
    # Duplicate detection is `duplicates.title_groups`, not a local title match.
    # It keys on title AND first-author surname and honours #dupok, neither of
    # which the original did -- on title alone it reported 7 groups over the live
    # library and 4 were different books that merely share a title (Lang vs Artin
    # *Algebra*, Friedberg vs Shilov *Linear Algebra*, Arthur vs Copi
    # *Introduction to Logic*, Corson vs Buchman *Stage Makeup*). No normaliser
    # change fixes those; the author has to be in the key.
    #
    # `db=` is threaded through so this reads the SAME library the records came
    # from. Without it `title_groups()` falls back to $CALIBRE_LIBRARY while
    # `load_books` was given an explicit library, and the report silently mixes
    # two catalogues: records from one, duplicate groups from the other.
    duplicate_groups = [
        [{"id": b.id, "title": b.title, "authors": b.authors_str} for b in group]
        for group in title_groups(db=library / "metadata.db")
    ]

    scan_targets = [b for b in books if not record_isbn(b) and pdf_paths(b)]
    scan_budget = len(scan_targets)
    if limit_scan > 0:
        scan_budget = min(scan_budget, limit_scan)
    scan_jobs = (
        [(book, pdf_paths(book)[0]) for book in scan_targets[:scan_budget]] if scan_missing else []
    )
    isbn_scan = run_pdf_scans(
        scan_jobs,
        pages_each_end=pages_each_end,
        scan_timeout=scan_timeout,
        scan_workers=scan_workers,
        progress_every=progress_every,
    )
    scan_by_id = {int(r["id"]): r for r in isbn_scan}

    # Annotated because the value types are heterogeneous (int, str, list, dict,
    # None). Without it a checker infers the union from the literal and then
    # rejects `"missing-isbn" in r["issues"]` below on the `int` member.
    records: list[dict[str, Any]] = []
    for book in books:
        isbn = record_isbn(book)
        issues = []
        if not isbn:
            issues.append("missing-isbn")
        if missing_publisher(book):
            issues.append("missing-publisher")
        if missing_pubdate(book):
            issues.append("missing-pubdate")
        if bad_author(book):
            issues.append("bad-author")
        if junk_title(book):
            issues.append("junk-title")
        if not book.tags:
            issues.append("missing-tags")

        records.append(
            {
                "id": book.id,
                "title": book.title,
                "authors": book.authors_str,
                "isbn": isbn,
                "publisher": book.publisher or "",
                "pubdate": book.pubdate or "",
                "tags": list(book.tags),
                "formats": [str(p) for p in book.formats],
                "format_exts": sorted(file_exts(book)),
                "issues": issues,
                "scan": scan_by_id.get(int(book.id)),
            }
        )

    issue_counts = collections.Counter(issue for r in records for issue in r["issues"])
    format_counts = collections.Counter(ext for r in records for ext in r["format_exts"])
    safe_isbn_lookup = [
        r
        for r in records
        if r["isbn"] and ("missing-publisher" in r["issues"] or "missing-pubdate" in r["issues"])
    ]
    extract_candidates = [
        r for r in records if "missing-isbn" in r["issues"] and "pdf" in r["format_exts"]
    ]
    scanned_with_isbn = [r for r in isbn_scan if r.get("isbns")]
    single_isbn_scan_hits = [r for r in isbn_scan if len(r.get("isbns") or []) == 1]
    multi_isbn_scan_hits = [r for r in isbn_scan if len(r.get("isbns") or []) > 1]
    scan_errors = [r for r in isbn_scan if r.get("error")]
    scan_timeouts = [r for r in scan_errors if r.get("error") == f"timeout after {scan_timeout}s"]
    no_isbn_no_error = [r for r in isbn_scan if not r.get("isbns") and not r.get("error")]

    return {
        "summary": {
            "books": len(records),
            "issue_counts": dict(issue_counts.most_common()),
            "format_counts": dict(format_counts.most_common()),
            "records_with_isbn": sum(1 for r in records if r["isbn"]),
            "safe_existing_isbn_lookup_candidates": len(safe_isbn_lookup),
            "pdf_isbn_extraction_candidates": len(extract_candidates),
            "scanned_pdf_candidates": len(isbn_scan),
            "scanned_pdfs_with_isbn": len(scanned_with_isbn),
            "single_isbn_scan_hits": len(single_isbn_scan_hits),
            "multi_isbn_scan_hits": len(multi_isbn_scan_hits),
            "no_isbn_no_error": len(no_isbn_no_error),
            "scan_errors": len(scan_errors),
            "scan_timeouts": len(scan_timeouts),
            "scan_timeout_seconds": scan_timeout if scan_missing else 0,
            "scan_limit": limit_scan if scan_missing else 0,
            "scan_workers": scan_workers if scan_missing else 0,
            "duplicate_title_groups": len(duplicate_groups),
        },
        "records": records,
        "safe_existing_isbn_lookup": safe_isbn_lookup,
        "pdf_isbn_extraction_candidates": extract_candidates,
        "isbn_scan": isbn_scan,
        "duplicate_title_groups": duplicate_groups,
    }


def short_record(r: dict[str, Any]) -> str:
    return f"- `{r['id']}` {r.get('title') or ''} — {r.get('authors') or ''}"


def write_markdown(report: dict[str, Any], path: Path) -> None:  # noqa: C901 - flat single-pass renderer: one section per report key, no nesting
    """Render the report for reading. Lists are capped because the point is a
    queue to work through, not a transcript of the catalogue — the JSON alongside
    it is complete, and every cap prints how many it elided."""
    summary = report["summary"]
    lines = [
        "# Calibre Metadata Audit",
        "",
        f"Generated: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## Summary",
        "",
        f"- Books: {summary['books']}",
        f"- Records with valid ISBN: {summary['records_with_isbn']}",
        "- Existing-ISBN metadata-fill candidates: "
        f"{summary['safe_existing_isbn_lookup_candidates']}",
        f"- PDF ISBN extraction candidates: {summary['pdf_isbn_extraction_candidates']}",
        f"- Scanned PDF candidates: {summary['scanned_pdf_candidates']}",
        f"- Scanned PDFs with ISBN found: {summary['scanned_pdfs_with_isbn']}",
        f"- Single-ISBN scan hits: {summary.get('single_isbn_scan_hits', 0)}",
        f"- Multi-ISBN scan hits: {summary.get('multi_isbn_scan_hits', 0)}",
        f"- No-ISBN/no-error scans: {summary.get('no_isbn_no_error', 0)}",
        f"- PDF scan errors: {summary.get('scan_errors', 0)}",
        f"- PDF scan timeouts: {summary.get('scan_timeouts', 0)}",
        f"- Duplicate normalized-title groups: {summary['duplicate_title_groups']}",
        "",
        "## Issue Counts",
        "",
    ]
    for issue, count in summary["issue_counts"].items():
        lines.append(f"- `{issue}`: {count}")
    lines.extend(["", "## Format Counts", ""])
    for ext, count in summary["format_counts"].items():
        lines.append(f"- `{ext}`: {count}")

    lines.extend(["", "## Existing-ISBN Fill Candidates", ""])
    for r in report["safe_existing_isbn_lookup"][:80]:
        lines.append(f"{short_record(r)} — ISBN `{r['isbn']}`; issues: {', '.join(r['issues'])}")
    if len(report["safe_existing_isbn_lookup"]) > 80:
        lines.append(f"- ... {len(report['safe_existing_isbn_lookup']) - 80} more")

    found = [r for r in report["isbn_scan"] if r.get("isbns")]
    lines.extend(["", "## ISBNs Found By PDF Scan", ""])
    if found:
        for r in found[:120]:
            lines.append(f"- `{r['id']}` {r.get('title')} — found {', '.join(r['isbns'])}")
    else:
        lines.append("- None in this scan.")

    problem_scan = [r for r in report["isbn_scan"] if r.get("error")]
    lines.extend(["", "## PDF Scan Errors", ""])
    if problem_scan:
        for r in problem_scan[:80]:
            lines.append(f"- `{r['id']}` {r.get('title')} — {r.get('error')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Bad/Junk Metadata Queue", ""])
    bad = [r for r in report["records"] if {"bad-author", "junk-title"} & set(r["issues"])]
    for r in bad[:120]:
        lines.append(f"{short_record(r)} — issues: {', '.join(r['issues'])}")
    if len(bad) > 120:
        lines.append(f"- ... {len(bad) - 120} more")

    lines.extend(["", "## Duplicate Title Groups", ""])
    for group in report["duplicate_title_groups"][:60]:
        lines.append("")
        for r in group:
            lines.append(short_record(r))
    if len(report["duplicate_title_groups"]) > 60:
        lines.append(f"- ... {len(report['duplicate_title_groups']) - 60} more groups")

    path.write_text("\n".join(lines) + "\n")


def audit(library: Path, **kw: Any) -> dict[str, Any]:
    """Load the catalogue and classify it. The one call a consumer needs."""
    return classify(load_books(db=library / "metadata.db"), library, **kw)


def _worker_main(argv: list[str] | None = None) -> int:
    """`python -m calibre_core.audit --scan-one-pdf <path>` — the scan subprocess.

    Deliberately minimal and separate from the `calibre-core audit` subcommand:
    this is an internal protocol between `scan_pdf_for_isbns` and its child, one
    JSON object on stdout, and it must not acquire flags or output modes that the
    parent does not send.
    """
    ap = argparse.ArgumentParser(prog="python -m calibre_core.audit", description=__doc__)
    ap.add_argument("--scan-one-pdf", type=Path, required=True)
    ap.add_argument("--pages-each-end", type=int, default=8)
    args = ap.parse_args(argv)
    scanned = _scan_pdf_text_layer(args.scan_one_pdf, args.pages_each_end)
    print(json.dumps(scanned, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_worker_main())
