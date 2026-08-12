"""Typo-tolerant scoring. Pure — operates on an in-memory list, touches no DB.

Calibre's own search does substring and regex but has no edit-distance matching,
so a misremembered spelling returns nothing at all:

    calibredb list --search 'title:perspektive'  -> 0 hits
    search('perspektive', books)                 -> every perspective book

Stdlib difflib rather than rapidfuzz: a ~1000-book library scans in well under a
second, and a zero-dependency core is what makes the dependency direction
enforceable.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from calibre_core.normalize import norm

DEFAULT_THRESHOLD = 0.55
DEFAULT_MARGIN = 0.15


def token_set_ratio(query: str, candidate: str) -> float:
    """Order-independent: each query token takes its best candidate token, averaged.

    Lets 'geometry of art' reach 'The Geometry of an Art'.
    """
    qt, ct = query.split(), candidate.split()
    if not qt or not ct:
        return 0.0
    return sum(max(SequenceMatcher(None, t, o).ratio() for o in ct) for t in qt) / len(qt)


def score(query: str, candidate: str) -> float:
    """Blend whole-string and token-set similarity; a substring hit is a free win."""
    if not candidate:
        return 0.0
    if query in candidate:
        return 1.0
    return max(
        SequenceMatcher(None, query, candidate).ratio(),
        token_set_ratio(query, candidate),
    )


def search(
    query: str,
    books,
    field: str = "both",
    limit: int = 10,
    threshold: float = DEFAULT_THRESHOLD,
    margin: float = DEFAULT_MARGIN,
) -> list[dict]:
    """Rank `books` against `query`. Each book: an object with id/title/authors.

    Two cutoffs, and the relative one is the important half. The token-set
    average inflates scores whenever a short query token resembles any token in
    an unrelated title ('kirsti' vs 'kristin' scores ~0.66), so an absolute floor
    alone leaves a long noisy tail. Real matches cluster well above the noise, so
    anything more than `margin` below the best hit is dropped. Pass margin=1.0 to
    disable and inspect the raw ranking.
    """
    q = norm(query)
    if not q:
        return []
    out: list[dict] = []
    for b in books:
        bid = b.id if hasattr(b, "id") else b[0]
        title = b.title if hasattr(b, "title") else b[1]
        authors = b.authors_str if hasattr(b, "authors_str") else (
            b.authors if hasattr(b, "authors") else b[2]
        )
        if not isinstance(authors, str):
            authors = " & ".join(authors)
        nt, na = norm(title), norm(authors)
        if field == "title":
            s = score(q, nt)
        elif field == "authors":
            s = score(q, na)
        else:
            s = max(score(q, nt), score(q, na), score(q, f"{nt} {na}"))
        if s >= threshold:
            out.append({"score": round(s, 3), "id": bid, "title": title, "authors": authors})
    out.sort(key=lambda r: (-r["score"], r["id"]))
    if out:
        cutoff = out[0]["score"] - margin
        out = [r for r in out if r["score"] >= cutoff]
    return out[:limit]
