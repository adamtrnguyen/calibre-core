# Salvage from `calibre-check-wip`

These five documents are what is kept from `calibre-check-wip`, the design workspace that
produced `calibre-core`. The workspace was **71 files / 1,789,139 B**, was **never under
version control**, and was deleted on **2026-08-12** after this relocation was verified
byte-identical.

Every file here carries an `ARCHIVED` header stating what in it has gone stale. Read those
headers before acting on any content — several of these documents read as live worklists
and are not.

| File | What it is | Live content? |
|---|---|---|
| `DECISIONS.md` | The 11 design decisions behind `calibre-core` + measured facts. The most valuable file here. | Blockers 1 and 3 re-verified and **resolved**; blockers 2, 4, 5, 6 unverified |
| `calibre_invariant_checker_design.md` | The v2 design doc the package was built from | Superseded by shipped code |
| `calibre_target_state.md` | Invariant catalogue (A/B/C/D/E) + detection strategies | Scope + OCR sections stale |
| `verify_A_integrity.md` | Point-in-time Class A verification run | Method yes, verdict is a snapshot |

Two further files travelled with this set and are not published — personal worklists with
no design content.

## Not preserved, and why

- **~30 scripts under `checks/`** — each opened `metadata.db` with a hand-rolled
  `sqlite3.connect` and carried its own normalisers. Superseded by `calibre_core`.
- **15 JSON artifacts** (`hydration.json`, `dataless_meta.json`, … ≈ 1.45 MB, the bulk of
  the workspace) — per-file OneDrive hydration snapshots. Regenerable from `stat` alone at
  no hydration cost (`st_blocks == 0` is an exact placeholder classifier, per
  `DECISIONS.md`), and they describe an eviction state that drifts. Their durable findings
  are already in `DECISIONS.md`.

## The one thing `norm.py` still did better: underscores

`checks/E_work/norm.py` was released for deletion after a final diff of its `surname()`
against the shipped `calibre_core.normalize.author_surname`, run over all **1,388** author
rows in the live library (not 1,122 — that is the *book* count).

**34 names differ. The shipped version is right or equivalent on 33 of them:**

- **21 hyphenated surnames** — `norm.py` collapses the hyphen (`arpacidusseau`,
  `muller-brockmann` → `mullerbrockmann`); shipped keeps it. Shipped is the correct name.
- **2 generational suffixes** — `norm.py` returns the suffix itself: `'David Bau III'` →
  `iii`, `'Hugh G. Gauch Jr.'` → `jr`. Shipped: `bau`, `gauch`.
- **2 CJK names** — `norm.py` returns the **empty string** (`'Kim Dongho (김동호)'` → `''`).
  This is the worst failure mode available: callers compare surnames for equality, so every
  CJK author collides as equal. Shipped preserves `김동호`.
- **Atomic-diacritic letters** — `'Syvert P. Nørsett'` → `nrsett` (drops `ø`),
  `'Paweł Łupkowski'` → `upkowski` (drops `Ł`). Shipped: `norsett`, `lupkowski`.
- Plus `'David F.Mayers'` → `fmayers` (no space after the initial's period), and `'ma2'` →
  `ma` (drops the digit). Shipped: `mayers`, `ma2`.

**The single case where `norm.py` is more faithful — the underscore:**

```
'IGGI_art'
    norm.py = 'iggiart'      <-- keeps the whole handle
    shipped = 'art'          <-- underscore becomes a space, 'iggi' is discarded
```

Shipped's `re.sub(rf"[^a-z0-9\s{CJK}-]+", " ", s)` allows hyphen but not underscore, so an
underscore separates tokens and the last-token rule throws away everything before it. For a
pseudonymous handle, `_` joins one name for exactly the reason the docstring already gives
for keeping `-` and deleting `'`.

**Impact is one author** — `IGGI_art` is the only underscore-containing author row in the
library (`WHERE name LIKE '%\_%' ESCAPE '\'`, 1 hit, author id 809). So this is a real but
tiny gap, recorded here rather than fixed: changing a released normaliser's output is not a
salvage-task decision. The fix, if wanted, is to add `_` to that character class and strip
it in the token cleanup alongside `-`.

## Also observed, unrelated to this task

Two author rows look like one name split on a comma during import:
`'TC Chen (施通'` and `'TC晨)'` (unbalanced parens, one name across two rows). That is a
library metadata defect, not a normaliser issue.
