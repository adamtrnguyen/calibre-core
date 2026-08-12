"""Old (calibre_mcp.matching) vs new (calibre_core), read-only, real library."""
import os, sys, json
os.environ["CALIBRE_LIBRARY"] = "/Users/adam/Calibre Library"
sys.path.insert(0, "/Users/adam/Research/MCPSuite/calibre-mcp/src")
sys.path.insert(0, "src")

from calibre_mcp import matching as old
from calibre_core.records import load_books as new_load
from calibre_core.search import search as new_search
from calibre_core import duplicates as newdup
from calibre_core.orphans import orphan_dirs as new_orphans
from calibre_core.normalize import dedup_key

fail = 0

# 1. load_books -> identical (id, title, authors) triples
o = sorted((b, t, a) for b, t, a in old.load_books())
n = sorted((b.id, b.title, b.authors_str) for b in new_load())
print(f"load_books: old={len(o)} new={len(n)}  identical={o == n}")
if o != n:
    fail += 1
    diff = [x for x in zip(o, n) if x[0] != x[1]][:3]
    for a, b in diff: print("   OLD", a, "\n   NEW", b)

# 2. search -> identical ordered results, scores to 3dp
books = new_load()
QUERIES = ["kirsti anderson", "perspektive", "konnen", "geometry of art",
           "fairchilde color apearance", "陶哲轩", "zzzqqq nonexistent", "vollmar optik"]
same = 0
for q in QUERIES:
    ro = old.fuzzy_search(q)
    rn = new_search(q, books)
    ko = [(r["id"], r["score"]) for r in ro]
    kn = [(r["id"], r["score"]) for r in rn]
    ok = ko == kn
    same += ok
    print(f"search {q!r:32} old={len(ro):>2} new={len(rn):>2} identical={ok}")
    if not ok:
        fail += 1
        print("    old:", ko[:5]); print("    new:", kn[:5])
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
import re
def has_cjk(s): return bool(re.search(f"[{CJK}]", s))
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
if oo != no: fail += 1

print(f"\n=== differential failures: {fail} ===")
