"""FROZEN pre-extraction reference. Do not "fix" anything in this file.

This is `calibre_mcp/src/calibre_mcp/matching.py` verbatim at commit 950dfde1,
the last commit BEFORE calibre-mcp was folded onto calibre-core (9d28836). It is
the baseline the golden differential compares against, and its value is entirely
in being unchanged -- editing it to agree with calibre_core would delete the only
evidence the extraction preserved behaviour.

It is vendored rather than imported from the consumer's working tree because that
tree no longer contains it: `calibre_mcp.matching` is now a dict adapter that
re-exports `load_books` straight from `calibre_core.records`. Pointed at the live
module the differential compared calibre_core against itself -- every check
tautologically identical -- and in fact crashed, because `old.load_books()` had
started returning `Book` objects that the script tried to unpack as 3-tuples. A
differential whose baseline can silently become the thing under test is not a
differential.

Self-contained by luck and worth keeping so: stdlib only, no calibre_mcp imports,
no rapidfuzz. So this file runs in calibre-core's dependency-free venv forever,
with no path injection into a sibling repo.

Original docstring follows.
---------------------------------------------------------------------------
Typo-tolerant matching over Calibre metadata.

Calibre's own search does substring and regex (`title:"~colou?r"`) but has no
edit-distance matching, so a misremembered spelling returns nothing:

    calibredb list --search 'title:perspektive'   -> 0 hits
    fuzzy_search('perspektive')                   -> every perspective book

Read-only against metadata.db. Stdlib only — no rapidfuzz dependency, which
matters because a 900-book library scans in well under a second anyway.
"""

from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / "Calibre Library"

# Ranges kept intact by normalisation: kana, CJK ext-A, CJK, hangul.
# Stripping these is the bug that made a Chinese analysis textbook cluster
# with three unrelated Japanese art books in an earlier dedup pass.
CJK = r"぀-ヿ㐀-䶿一-鿿가-힯"


def library_path() -> Path:
    return Path(os.environ.get("CALIBRE_LIBRARY", str(DEFAULT_LIBRARY)))


def db_path() -> Path:
    return library_path() / "metadata.db"


_CJK_CHAR = re.compile(rf"[{CJK}]")


def fold_diacritics(s: str) -> str:
    """Strip Latin diacritics WITHOUT touching CJK.

    Keeping CJK in the allowed class above is not sufficient on its own,
    because a blanket NFKD corrupts CJK before that class is ever applied:

    * Hangul syllables DECOMPOSE into conjoining Jamo (U+1100 block), which
      `CJK` does not cover, so Korean collapsed to '' anyway. `한` -> U+1112
      U+1161 U+11AB. Library book 393 carries a Korean title.
    * Kana voicing marks decompose into a combining mark that the strip then
      DELETES: `ドキドキ` -> `トキトキ`, `が` -> `か` (books 922, 923). Voicing is
      phonemic, so that is a different word and distinct titles can collide.

    So decompose per character and skip CJK. Non-CJK still goes through NFKD,
    which is what folds Können -> konnen and maps halfwidth kana onto fullwidth.
    NFC first because macOS stores filenames decomposed.

    Mirrors calibre_core.normalize._fold_diacritics. Duplicated only until this
    server switches to that package -- keep the two in step.
    """
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", s or ""):
        if _CJK_CHAR.match(ch):
            out.append(ch)
            continue
        d = unicodedata.normalize("NFKD", ch)
        out.append("".join(c for c in d if not unicodedata.combining(c)))
    return "".join(out)


def norm(s: str) -> str:
    """Casefold, strip diacritics, drop punctuation. Keeps CJK codepoints.

    Andersen / Können / Bläsing all normalise to unaccented forms, so a query
    typed on a US keyboard still reaches them.
    """
    return re.sub(rf"[^a-z0-9{CJK}]+", " ", fold_diacritics(s).casefold()).strip()


def token_set_ratio(q: str, c: str) -> float:
    """Order-independent: each query token scores its best candidate token,
    averaged. Lets 'geometry of art' reach 'The Geometry of an Art'."""
    qt, ct = q.split(), c.split()
    if not qt or not ct:
        return 0.0
    return sum(max(SequenceMatcher(None, t, o).ratio() for o in ct) for t in qt) / len(qt)


def score(query: str, candidate: str) -> float:
    """Blend whole-string and token-set similarity; substring is a free win."""
    if not candidate:
        return 0.0
    if query in candidate:
        return 1.0
    return max(
        SequenceMatcher(None, query, candidate).ratio(),
        token_set_ratio(query, candidate),
    )


def load_books(db: Path | None = None) -> list[tuple[int, str, str]]:
    db = db or db_path()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(
            """
            SELECT b.id, b.title, COALESCE(GROUP_CONCAT(a.name, ' & '), '')
            FROM books b
            LEFT JOIN books_authors_link l ON l.book = b.id
            LEFT JOIN authors a ON a.id = l.author
            GROUP BY b.id
            """
        ).fetchall()
    finally:
        con.close()


def fuzzy_search(
    query: str,
    field: str = "both",
    limit: int = 10,
    threshold: float = 0.55,
    margin: float = 0.15,
) -> list[dict]:
    """Absolute threshold plus a relative cutoff.

    The token-set average inflates scores when a short query token resembles
    any token in an unrelated title ("kirsti" vs "kristin" ~0.66), so an
    absolute floor alone leaves a long noisy tail. Real matches cluster well
    above the noise, so anything more than `margin` below the best hit is
    dropped. Pass margin=1.0 to disable and inspect the raw ranking.
    """
    q = norm(query)
    if not q:
        return []
    out: list[dict] = []
    for bid, title, authors in load_books():
        nt, na = norm(title), norm(authors)
        if field == "title":
            s = score(q, nt)
        elif field == "authors":
            s = score(q, na)
        else:
            s = max(score(q, nt), score(q, na), score(q, f"{nt} {na}"))
        if s >= threshold:
            out.append({"score": round(s, 3), "id": bid, "title": title,
                        "authors": authors})
    out.sort(key=lambda r: (-r["score"], r["id"]))
    if out:
        cutoff = out[0]["score"] - margin
        out = [r for r in out if r["score"] >= cutoff]
    return out[:limit]


def duplicate_groups() -> list[list[dict]]:
    """Title-normalised duplicate detection.

    Strips parentheticals, edition markers and 'solutions manual' before
    grouping. Same-title-different-author pairs (Lang vs Artin *Algebra*) and
    textbook/solutions-manual pairs are reported, not deleted — they are
    legitimately distinct books and the caller must judge.
    """
    def key(t: str) -> str:
        t = re.sub(r"\(.*?\)|\[.*?\]|（.*?）", "", (t or "").lower())
        t = re.sub(r"\b(the|a|an|edition|ed|\d+(st|nd|rd|th)|si|solutions?|manual)\b", " ", t)
        return re.sub(rf"[^a-z0-9{CJK}]+", " ", t).strip()

    groups: dict[str, list[dict]] = {}
    for bid, title, authors in load_books():
        k = key(title)
        if not k:
            continue
        groups.setdefault(k, []).append({"id": bid, "title": title, "authors": authors})
    return [v for v in groups.values() if len(v) > 1]


def find_orphans() -> list[dict]:
    """Directories in the library tree with no matching row in metadata.db.

    These appear after a metadata.db rollback: the catalog reverts but the
    filesystem does not. Orphans are invisible to the Calibre GUI and to
    `calibredb list`, yet a directory walk still finds them — so omnirag
    ingests them while the UUID resolver returns None, silently costing those
    books their calibre:// deep links.
    """
    lib = library_path()
    con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT id FROM books")}
    finally:
        con.close()
    out = []
    for d in sorted(lib.glob("*/*")):
        m = d.is_dir() and re.search(r"\((\d+)\)$", d.name)
        if m and int(m.group(1)) not in have:
            files = [f.name for f in d.iterdir()
                     if f.suffix.lower() in (".pdf", ".epub", ".djvu", ".mobi", ".azw3")]
            out.append({"id": int(m.group(1)), "path": str(d.relative_to(lib)),
                        "files": files})
    return out
