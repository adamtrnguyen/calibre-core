# One calibre-core: the divergent copies it replaced

Moved here from the README, module docstrings and comments, where it read as a claim about the
present. The code now states only the rule each copy got wrong.

## 2026-08-12: the normalisers (from `README.md` and `domain/normalize.py`)

The same logic had been reimplemented across 15 files in 4 repos, with three divergent title
normalisers. The differences were silent:

| Copy | Behaviour | Effect |
|---|---|---|
| calibre-mcp `norm` | NFKD + casefold, CJK preserved | correct |
| calibre-mcp `duplicate_groups` | `.lower()`, no NFKD, CJK preserved | search and dedup disagreed on accents |
| omni-rag `normalized_title` | `.lower()`, `[^a-z0-9]`, no CJK | every CJK title keyed to `''` |

The callers filtered `if key`, so the CJK books were not mis-grouped but absent from duplicate
detection, with no error anywhere. Real ids 25, 803, 921 and 922 all keyed to `''`. Fixed
2026-08-12; omni-rag now mirrors `normalize.py`.

A fourth copy then turned up that no design doc had named:
`omni-rag/scripts/calibre_openlibrary_metadata_candidates.py`, matching Open Library candidates
on `[^a-z0-9]` with no NFKD, so `Juan M. Durán` keyed to the single letter `n`.

A duplicate check also keyed on title alone, so *Algebra* by Lang and *Algebra* by Artin were the
same book. `author_surname` is the discriminator that fixed it.

`test_normalize.py` exists because a test asserting only Chinese passed for months while Korean
was still broken (NFKD decomposes Hangul into conjoining Jamo outside the CJK class).

## 2026-08-12: the read-only connection (from `README.md` and `infrastructure/sqlite.py`)

Before `connect()` existed, `?mode=ro` was copy-pasted at five sites in calibre-mcp and two more
in omni-rag's UUID resolver, and one script opened the same `metadata.db` with a bare read-write
`sqlite3.connect()` for a `SELECT`.

## 2026-08-12: ISBN validation (from `domain/isbn.py`, commit f6f5461)

`isbn.py` was hand-rolled: about 25 lines reimplementing the ISBN-10 and ISBN-13 checksums. It was
replaced by a thin wrapper over `isbnlib`, which also canonicalises and hyphenates.

## 2026-08-12: pairwise `#dupok` (from `domain/duplicates.py`, commit 9e0b981)

`calibre_mcp.writes` flattened every `#dupok` partner into one global set and suppressed on mere
membership. If any unrelated record named two books as its partners, a genuine duplicate between
those two books reported `ok`: the write gate waved through the duplicate it exists to catch, and
nothing reported that it had. It was fixed locally as `writes._excused_within`, and omni-rag's
audit grew its own copy. `excused_within` is the shared version both collapsed into.

## 2026-08-13: the audit and the Open Library matcher (commit 7026639, v0.4.0)

`application/audit.py` moved here from omni-rag's `scripts/`, where it was 425 lines of pure
Calibre logic in a retrieval repo; it imported only `calibre_core` and the standard library.

`openlibrary.author_overlap` kept empty author keys, so two unrelated CJK authors (both unkeyable)
scored an overlap of 1. Empty keys are now dropped.
