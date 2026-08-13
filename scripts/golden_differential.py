"""Old vs new, read-only, against the real library.

"Old" is `scripts/golden/pre_extraction_matching.py` -- a frozen copy of
calibre-mcp's `matching.py` from BEFORE the extraction (commit 950dfde1). It used
to be imported live from the calibre-mcp working tree, but that module is now a
dict adapter over calibre_core, so the baseline had quietly become the thing under
test: every check would compare calibre_core against itself and report
"identical". See the frozen file's header.

Run from the package root: `uv run python scripts/golden_differential.py`
"""
import os
import re
import sys
from pathlib import Path

os.environ["CALIBRE_LIBRARY"] = "/Users/adam/Calibre Library"
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "golden"))
sys.path.insert(0, str(_HERE.parent / "src"))

import pre_extraction_matching as old

from calibre_core import __version__
from calibre_core import duplicates as newdup
from calibre_core.normalize import author_surname
from calibre_core.orphans import orphan_dirs as new_orphans
from calibre_core.records import load_books as new_load
from calibre_core.search import search as new_search

print(f"calibre_core {__version__} vs frozen pre-extraction baseline (950dfde1)\n")
fail = 0

# 1. load_books -> identical (id, title, authors) triples
o = sorted((b, t, a) for b, t, a in old.load_books())
n = sorted((b.id, b.title, b.authors_str) for b in new_load())
print(f"load_books: old={len(o)} new={len(n)}  identical={o == n}")
if o != n:
    fail += 1
    # strict=False on purpose: old and new can legitimately differ in LENGTH
    # (the counts above are printed separately), and this pairwise walk exists to
    # show the first few disagreeing rows, not to assert the lengths match.
    diff = [x for x in zip(o, n, strict=False) if x[0] != x[1]][:3]
    for a, b in diff:
        print("   OLD", a, "\n   NEW", b)

# 2. search -> identical ordered results, scores to 3dp
books = new_load()
# Every misspelling here is DELIBERATE — the point is that fuzzy search still
# finds Andersen, Fairchild, "Perspektive" and "Können" through them. The inline
# directive below is what stops codespell from "fixing" the fixture and quietly
# turning a typo-tolerance check into an exact-match one.
QUERIES = ["kirsti anderson", "perspektive", "konnen", "geometry of art",
           "fairchilde color apearance",  # codespell:ignore
           "陶哲轩", "zzzqqq nonexistent", "vollmar optik"]
same = 0
for q in QUERIES:
    ro = old.fuzzy_search(q)
    rn = new_search(q, books)
    ko = [(r["id"], r["score"]) for r in ro]
    kn = [(r["id"], r["score"]) for r in rn]
    ok = ko == kn
    same += ok
    io, inn = {i for i, _ in ko}, {i for i, _ in kn}
    lost, gained = io - inn, inn - io
    verdict = "identical" if ok else ("GAINED-ONLY" if not lost else "LOST RESULTS")
    print(f"search {q!r:32} old={len(ro):>2} new={len(rn):>2} {verdict}")
    if lost:
        # Losing a result is a regression; gaining one is not. The old flat
        # equality check reported both as failure, which hid the distinction.
        fail += 1
        print("    LOST:", sorted(lost))
    elif gained:
        print("    gained (not a regression):", sorted(gained))
print(f"search: {same}/{len(QUERIES)} identical")

# 3. duplicate_groups -> new must be a SUPERSET whose additions are only CJK
def idsets(groups, key):
    return {frozenset(key(x) for x in g) for g in groups}
og = idsets(old.duplicate_groups(), lambda d: d["id"])
ng = idsets(newdup.title_groups(books, respect_dupok=False), lambda b: b.id)
lost, gained = og - ng, ng - og
print(f"\ndup groups: old={len(og)} new={len(ng)} lost={len(lost)} gained={len(gained)}")
titles = {b.id: b.title for b in books}
CJK = "぀-ヿ㐀-䶿一-鿿가-힯"


def has_cjk(s):
    return bool(re.search(f"[{CJK}]", s))


for g in sorted(lost, key=lambda s: min(s))[:12]:
    ts = [titles[i] for i in sorted(g)]
    print("  LOST  ", sorted(g), [t[:38] for t in ts])
for g in sorted(gained, key=lambda s: min(s))[:12]:
    ts = [titles[i] for i in sorted(g)]
    mark = "CJK" if any(has_cjk(t) for t in ts) else "NON-CJK"
    print(f"  GAINED[{mark}]", sorted(g), [t[:34] for t in ts])

# 4. orphans -> identical
oo = sorted((d["id"], d["path"]) for d in old.find_orphans())
no = sorted((d["id"], d["path"]) for d in new_orphans())
print(f"\norphans: old={len(oo)} new={len(no)} identical={oo == no}")
if oo != no:
    fail += 1

# 5. author_surname -> report every real author the 0.2.0 fixes moved.
#    NOT a pass/fail check: the fixes are deliberate behaviour changes. It exists
#    so a change to the surname logic cannot alter the discriminator for a real
#    author without that showing up here by name.
EXPECTED_SURNAME_FIXES = {
    "Daniel J. O'Keefe": "okeefe",
    "David R. O'Hallaron": "ohallaron",
    "Joseph D'Amelio": "damelio",
    "Marie O'Mahony": "omahony",
    # Not a personal name (a magazine, filed as an author). Listed because the
    # apostrophe fix touches it and 'how' is what it keyed to before as well.
    "Hair's How": "how",
    "Syvert P. Nørsett": "norsett",
    "Paweł Łupkowski": "lupkowski",
}
print("\nauthor_surname: records the 0.2.0 apostrophe + stroke-letter fixes touch")
for name, want in sorted(EXPECTED_SURNAME_FIXES.items()):
    got = author_surname(name)
    flag = "ok " if got == want else "DIFF"
    print(f"  {flag} {name!r:26} -> {got!r} (expected {want!r})")
    if got != want:
        fail += 1

print(f"\n=== differential failures: {fail} ===")
