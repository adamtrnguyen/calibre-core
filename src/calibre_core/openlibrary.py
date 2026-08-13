"""Open Library metadata candidates for records an audit flagged.

Second half of the ISBN-first cleanup: `audit.py` finds records whose metadata is
thin and reports the ISBNs it can find, this looks each one up and scores how
well the edition it gets back matches the record we already have.

READ-ONLY, and deliberately a *candidate* generator rather than a fixer. Every
row carries a `status` a human acts on:

  strong-candidate     title similarity >= 0.74 AND at least one author surname
                       in common (or the record has no author at all to check).
  review-title-author  the ISBN resolved, but the edition does not look like the
                       book we hold. Usually a reprint, an omnibus, or an ISBN
                       typed off the wrong copyright line.
  lookup-error         Open Library had nothing, or the network did not answer.

Nothing here writes to Calibre. Applying a candidate goes through
`writes.set_book_metadata`, which merges identifiers and refuses title/author.

Why Open Library and not Calibre's own `fetch-ebook-metadata`: no API key, no
plugin, and its edition endpoint answers the exact question — "what edition is
this ISBN" — instead of a fuzzy title search. Calibre's Google plugin also
returns HTTP 500s against the legacy Books feed on this machine.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent import futures
from pathlib import Path
from typing import Any

from calibre_core.normalize import author_surname, norm

OPENLIBRARY = "https://openlibrary.org"
USER_AGENT = "calibre-core-metadata-candidates/1.0"

# Above this, the fetched edition is treated as the same work. Set from the live
# library: real matches cluster well above it once `norm` has folded case,
# accents and punctuation, while reprints and omnibuses fall below.
TITLE_MATCH_THRESHOLD = 0.74


def title_similarity(left: str, right: str) -> float:
    """Ratio over NORMALISED titles, so casing, accents and punctuation don't
    count as differences — that is what `norm` is for, and comparing raw strings
    scored `Pólya` against `Polya` as a mismatch."""
    return difflib.SequenceMatcher(None, norm(left), norm(right)).ratio()


def split_authors(value: str) -> list[str]:
    """Split the house `A & B` form, and also `A and B` — Open Library and
    copyright pages use the word."""
    return [a.strip() for a in re.split(r"\s*&\s*|\s+and\s+", value or "") if a.strip()]


def author_key(value: str) -> str:
    """Key ONE already-split author name for set-intersection matching.

    `author_surname` takes the first author of an `&`-joined string; here
    `split_authors` has already split, on `&` AND ' and ', so each call gets a
    single name and the `&` handling is a harmless no-op.
    """
    return author_surname(value)


def author_overlap(current: str, candidate: list[str]) -> int:
    # Empty keys are dropped: an unkeyable name must not match another unkeyable
    # name, which is how two unrelated CJK authors used to score an overlap of 1.
    current_keys = {key for a in split_authors(current) if (key := author_key(a))}
    candidate_keys = {key for a in candidate if (key := author_key(a))}
    return len(current_keys & candidate_keys)


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class OpenLibraryClient:
    """Edition lookups, with author names resolved and cached.

    Open Library's edition JSON gives authors as `/authors/OL123A` keys, not
    names, so each edition costs one extra request per distinct author. The cache
    is what makes a few hundred lookups tolerable: a prolific author appears
    across many editions and is fetched once. It is lock-guarded because the
    caller drives this from a thread pool.
    """

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        self._author_cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def author_name(self, key: str) -> str:
        if not key:
            return ""
        with self._lock:
            if key in self._author_cache:
                return self._author_cache[key]

        try:
            data = fetch_json(f"{OPENLIBRARY}{key}.json", timeout=self.timeout)
            name = str(data.get("name") or "")
        except Exception:  # noqa: BLE001 - a missing name must not fail the edition
            name = ""

        with self._lock:
            self._author_cache[key] = name
        return name

    def edition(self, isbn: str) -> dict[str, Any]:
        url_isbn = urllib.parse.quote(isbn)
        try:
            data = fetch_json(f"{OPENLIBRARY}/isbn/{url_isbn}.json", timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            return {"isbn": isbn, "error": f"http {exc.code}"}
        except Exception as exc:  # noqa: BLE001 - report lookup failures
            return {"isbn": isbn, "error": f"{type(exc).__name__}: {exc}"}

        author_keys = [a.get("key", "") for a in data.get("authors") or [] if isinstance(a, dict)]
        authors = [name for key in author_keys if (name := self.author_name(key))]
        publishers = [str(p).strip() for p in data.get("publishers") or [] if str(p).strip()]
        languages = [
            str(lang.get("key", "")).removeprefix("/languages/")
            for lang in data.get("languages") or []
            if isinstance(lang, dict)
        ]
        return {
            "isbn": isbn,
            "openlibrary_key": data.get("key") or "",
            "title": data.get("title") or "",
            "subtitle": data.get("subtitle") or "",
            "full_title": data.get("full_title") or "",
            "authors": authors,
            "publishers": list(dict.fromkeys(publishers)),
            "publish_date": data.get("publish_date") or "",
            "number_of_pages": data.get("number_of_pages"),
            "languages": sorted({lang for lang in languages if lang}),
            "isbn_10": data.get("isbn_10") or [],
            "isbn_13": data.get("isbn_13") or [],
        }


def candidate_rows(report: dict[str, Any], include_multi: bool = False) -> list[dict[str, Any]]:
    """Audit report -> the rows worth looking up, one per book id.

    Two sources, and the ORDER matters: a record's own Calibre ISBN wins over one
    scraped off a copyright page, because the catalogue value was entered
    deliberately. `setdefault` on the scan pass is what enforces that.

    A multi-ISBN scan hit is skipped by default. A copyright page listing several
    ISBNs is usually listing the hardback, paperback and ebook of the same work —
    but sometimes it is a series page for other titles, and picking the first is
    then simply wrong. `include_multi` takes the first anyway and labels the row
    `pdf-scan-multi-isbn-first` so the guess stays visible downstream.
    """
    rows: dict[int, dict[str, Any]] = {}
    for record in report.get("safe_existing_isbn_lookup") or []:
        rows[int(record["id"])] = {
            "id": record["id"],
            "title": record.get("title") or "",
            "authors": record.get("authors") or "",
            "isbn": record.get("isbn") or "",
            "source": "existing-calibre-isbn",
            "issues": record.get("issues") or [],
        }

    for scan in report.get("isbn_scan") or []:
        isbns = scan.get("isbns") or []
        if len(isbns) == 1 or (include_multi and isbns):
            rows.setdefault(
                int(scan["id"]),
                {
                    "id": scan["id"],
                    "title": scan.get("title") or "",
                    "authors": scan.get("authors") or "",
                    "isbn": isbns[0],
                    "all_found_isbns": isbns,
                    "source": (
                        "pdf-scan-single-isbn" if len(isbns) == 1 else "pdf-scan-multi-isbn-first"
                    ),
                    "issues": ["missing-isbn"],
                },
            )
    return list(rows.values())


def enrich(row: dict[str, Any], client: OpenLibraryClient) -> dict[str, Any]:
    """Look one row up and score the match.

    The title is compared against every form Open Library offers — `title`,
    `full_title`, and `title: subtitle` — and the BEST scores, because which
    field carries the subtitle is inconsistent across editions. A record titled
    *Statistical Rethinking* scores badly against a `full_title` of *Statistical
    Rethinking: A Bayesian Course* and well against the bare `title`; another
    edition puts it the other way round.

    A record with no author at all cannot fail the author check, so it passes on
    title alone — that is the whole population this tool exists to fix.
    """
    edition = client.edition(row["isbn"])
    result = {**row, "openlibrary": edition}
    if edition.get("error"):
        result["status"] = "lookup-error"
        return result

    titles = [edition.get("title") or "", edition.get("full_title") or ""]
    if edition.get("subtitle"):
        titles.append(f"{edition.get('title', '')}: {edition['subtitle']}")
    best_title_score = max(title_similarity(row["title"], title) for title in titles if title)
    overlap = author_overlap(row["authors"], edition.get("authors") or [])
    result["title_similarity"] = round(best_title_score, 3)
    result["author_overlap"] = overlap
    strong = best_title_score >= TITLE_MATCH_THRESHOLD and (
        overlap > 0 or not split_authors(row["authors"])
    )
    result["status"] = "strong-candidate" if strong else "review-title-author"
    return result


def build(
    audit_report: dict[str, Any],
    timeout: int = 12,
    workers: int = 4,
    limit: int = 0,
    include_multi: bool = False,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Enrich every candidate in `audit_report`, concurrently.

    Sorted by (status, id) at the end so `strong-candidate` rows lead the report
    and the order is stable between runs — an audit is something you diff against
    the last one.
    """
    rows = candidate_rows(audit_report, include_multi=include_multi)
    if limit > 0:
        rows = rows[:limit]

    client = OpenLibraryClient(timeout=timeout)
    candidates: list[dict[str, Any]] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        pending = [executor.submit(enrich, row, client) for row in rows]
        for i, future in enumerate(futures.as_completed(pending), start=1):
            candidates.append(future.result())
            if on_progress and (i == 1 or i == len(rows) or i % 25 == 0):
                on_progress(i, len(rows))

    candidates.sort(key=lambda r: (str(r.get("status")), int(r["id"])))
    counts: dict[str, int] = {}
    for row in candidates:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    return {"summary": {"input_candidates": len(rows), **counts}, "candidates": candidates}


def short_authors(authors: list[str]) -> str:
    """Three names then a count. An Open Library edition can list a dozen
    contributors, which turns one table row into a wrapped paragraph."""
    if not authors:
        return ""
    if len(authors) <= 3:
        return " & ".join(authors)
    return " & ".join(authors[:3]) + f" & +{len(authors) - 3}"


def write_markdown(report: dict[str, Any], path: Path) -> None:
    """Render as a table for side-by-side comparison — the decision this supports
    is "is the fetched edition the book I hold", which needs both values on one
    line. Cell values are pipe-escaped or the table silently gains columns."""
    summary = report["summary"]
    lines = [
        "# Open Library Metadata Candidates",
        "",
        f"Generated: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")

    lines.extend(
        [
            "",
            "## Candidates",
            "",
            (
                "| ID | Status | Source | ISBN | Current Title | Open Library Title | "
                "Open Library Authors | Publisher | Date |"
            ),
            "|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in report["candidates"]:
        ol = row.get("openlibrary") or {}
        values = [
            str(row["id"]),
            row.get("status", ""),
            row.get("source", ""),
            row.get("isbn", ""),
            row.get("title", ""),
            ol.get("full_title") or ol.get("title") or "",
            short_authors(ol.get("authors") or []),
            ", ".join((ol.get("publishers") or [])[:2]),
            ol.get("publish_date") or "",
        ]
        escaped = [v.replace("|", "\\|").replace("\n", " ") for v in values]
        lines.append("| " + " | ".join(escaped) + " |")
    path.write_text("\n".join(lines) + "\n")
