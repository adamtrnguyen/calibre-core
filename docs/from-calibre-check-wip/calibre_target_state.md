# Calibre library — target state specification

> **ARCHIVED — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/reports/`
> (deleted; was never under version control).** Kept as the invariant catalogue (A/B/C/D/E
> IDs and their detection strategies) that `calibre-core` was built against.
>
> **Two known stale points, per `DECISIONS.md` in this directory:**
> 1. **Scope.** Decision 8 re-scoped the checker from this document's ~24 invariants down to
>    ~6, on the measured grounds that six of them find *zero* violations and the defect rate
>    is 1/1,094. This spec is the wish list, not what shipped.
> 2. **OCR (lines ~112-113) is superseded.** This spec prescribes local
>    `ocrmypdf --skip-text --optimize 0`; the OCR route was settled as **ARC with a model**.
>    Do not run the local flags from this file. (This is the third leg of the "three-way OCR
>    contradiction" in `DECISIONS.md` blocker 4 — the skill was already corrected, this file
>    never was.)
>
> Header counts below (1,094 books / max id 1174) were true earlier on 2026-08-12 and have
> since moved — the library was at **1,122 books / max id 1206** when this was archived.

Library: `/Users/adam/Calibre Library` · 1,094 books, max id 1174 (2026-08-12)

This is the definition every verifier checks against. It is a **specification of the
desired end state**, plus the detection strategy for each invariant. Verifiers are
READ-ONLY: they report violations, they never fix them. Convergence happens in
reviewed, backed-up write batches afterwards.

Each invariant has: an ID, the rule, how to detect it, and its **severity**:
- **BLOCKER** — data loss or invisibility. Fix always.
- **DEFECT** — wrong/misleading data. Fix unless a documented exception applies.
- **HYGIENE** — inconsistency. Fix in bulk, low risk.
- **REPORT-ONLY** — cannot be auto-decided; a human picks.

---

## Class A — Integrity (files and rows agree)

| ID | Rule | Detection | Severity |
|---|---|---|---|
| **A1** | Every DB row has ≥1 format, and every format path exists on disk. | `calibredb list --fields id,formats --for-machine`, then `os.path.exists` each. | BLOCKER |
| **A2** | Every directory under the library holding a book file has a DB row. No orphans. | Walk `*/*/` dirs; compare against `SELECT path FROM books`. Orphans are invisible to the GUI and to `calibredb list`, but omni-rag still ingests them and the UUID resolver returns `None`, so they silently lose their `calibre://` deep links. | BLOCKER |
| **A3** | No two records reference the same file on disk. | Group format paths; any path with >1 owning id. | BLOCKER |
| **A4** | The on-disk `Author/Title (id)/` path matches the record's current title/author. | Compare `books.path` against a slugified `authors`/`title`. Drift comes from retitles and from metadata.db rollbacks. | DEFECT |
| **A5** | No zero-byte or stub files. A ~114-byte "PDF" is an Anna's 429 error body, not a book. | `stat` every format; flag <10 KB, and any PDF whose first bytes are not `%PDF`. | BLOCKER |

**Known A2 violation at spec time:** `Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)`
— pre-existing, predates 2026-08-12.

---

## Class B — Metadata correctness

| ID | Rule | Detection | Severity |
|---|---|---|---|
| **B1** | Title is the clean canonical title. No edition suffix, no format junk, no source watermark. | Regex titles for `\((\d+(st\|nd\|rd\|th)\|[0-9]+)?\s*(ed\.?\|edition)\)`, `PDF`, `EPUB`, `z-lib`, `libgen`, `retail`, `\(scan\)`, trailing version numbers. **Edition belongs in the file and in `comments`, not the title.** | DEFECT |
| **B2** | Authors are `First Last`, ampersand-separated, and **not inverted**. | ⚠ `calibredb add` with no `--authors` parses the FILENAME as `Title - Author`, while most sources name files `Author - Title` — so records land with title and author swapped. Detect: author field containing tell-tale title words (`the`, `introduction`, `guide`, `handbook`, `:`), or a title that looks like a personal name (2–3 capitalised tokens, no article). | DEFECT |
| **B3** | ≥1 identifier (`isbn` preferred, else `doi`/`asin`) where the work has one. | `SELECT book FROM identifiers` vs all ids. Government reports, free monographs and grey literature legitimately have none — exempt those. | HYGIENE |
| **B4** | `pubdate` is the real publication date, or NULL. It must **not** be the PDF's `CreationDate`. | ⚠ **Confirmed bug 2026-08-12:** record 1128 (Ching *AFSO*, genuinely 5th ed. 2023) carries `pubdate 2025-09-15`, which is the scan timestamp. Detect: `pubdate` later than a plausible publication year, `pubdate` equal to a file's `CreationDate`, or the Calibre sentinel `0101-01-01`. Cross-check against the edition statement inside the file. | DEFECT |
| **B5** | `languages` is set. | `SELECT book FROM books_languages_link`. | HYGIENE |
| **B6** | Series/`series_index` set only where a real series exists, and consistent within it. | Group by series; flag gaps, duplicate indices, single-member series. | HYGIENE |

---

## Class C — Tags (controlled vocabulary)

| ID | Rule | Detection | Severity |
|---|---|---|---|
| **C1** | Every record has ≥1 tag. | Books with no `books_tags_link` row. **Known: 11 tagless records** (106, 202, 235, 273, 334, 337, 401, 402, 460, 471, 519). | HYGIENE |
| **C2** | Tags are lowercase-hyphen, `and` spelled out rather than `&`. | Regex tag names for uppercase, spaces, underscores, `&`. | HYGIENE |
| **C3** | No junk tags: URLs, BISAC codes (`^[A-Z]{3}\d{6}`), auto-added `Calibre`/`General`. | Pattern match the tag table. | HYGIENE |
| **C4** | No synonym pairs — one concept, one tag. | Cluster tag names by edit distance and by co-occurrence. **Live suspects: `color`/`color-theory`/`color-and-light`; `vision`/`visual-perception`/`perception`; `design`/`product-design`; `systems`/`systems-thinking`.** Report clusters with a recommended canonical form; do NOT merge unreviewed — merging is lossy and irreversible. | REPORT-ONLY |
| **C5** | Tags come from the documented taxonomy, or are deliberate additions. | Diff live tag list against the `calibre` skill's taxonomy. New as of 2026-08-12 and intentional: `anthropometry`, `ergonomics`, `art-conservation`, `pigments`, `rendering`, `additive-manufacturing`, `textiles`, `problem-structuring`. | REPORT-ONLY |

---

## Class D — File quality (fitness for purpose)

The library exists to be *read and searched*, including by omni-rag semantic search.
A file that cannot be searched is a silent failure — it looks fine in the GUI.

| ID | Rule | Detection | Severity |
|---|---|---|---|
| **D1** | A PDF has a real text layer. | ⚠ **Confirmed failure 2026-08-12:** Ching *Visual Dictionary* had **336 characters across 336 pages** — page numbers only. Threshold: **< 500 chars per 100 pages ⇒ image-only.** For speed, sample rather than extracting whole books: `pdftotext -f N -l N+4` at ~4 spread-out offsets, and scale. | DEFECT (fixable by OCR) |
| **D2** | The text layer covers the **whole** book, not just part. | ⚠ **Confirmed failure 2026-08-12:** Bigio (id 1158) has a healthy 1.17 M-char total that hides **200 completely textless leading pages**. A total is not evidence of coverage. Sample at ≥4 offsets across the book and report **per-offset** density. | DEFECT (fixable by OCR) |
| **D3** | No truncation. | PDF: `pdfinfo` succeeds and the final page yields content or is a genuine blank end-leaf. EPUB: `zipfile.ZipFile().testzip()` — ⚠ **`ebook-meta` reading the title is NOT a validity test**; a truncated EPUB missing its end-of-central-directory still reports a title. Cross-check size against any recorded source size. | BLOCKER |
| **D4** | Scans of table/plate-dependent works are high enough resolution to be usable. | `pdfimages -list` sampled ppi. **< 150 ppi is marginal; ~96 ppi fails** for equations, dimension tables, and fine plates. Known: id 1158 Bigio is a 96-ppi raster throughout. | REPORT-ONLY |
| **D5** | OCR that exists is trustworthy for the record's purpose. | ⚠ NASA RP-1024 Vol I (id 1171): prose OCRs fine but **table numerals are silently dropped** — the percentile values, i.e. the entire point of the book. Any data-table reference whose numerals don't extract must carry a `comments` warning so nobody greps it and trusts the result. | REPORT-ONLY |

---

## Class E — Duplicates

| ID | Rule | Detection | Severity |
|---|---|---|---|
| **E1** | No two records are the same work in the same edition. | Normalise title (lowercase, strip subtitle after `:`, strip edition/punctuation) + first author surname; group. Also group by ISBN, and by exact file size. | REPORT-ONLY |
| **E2** | **Intentional pairs must NOT be reported as duplicates.** | See allowlist below. | — |

### Intentional-pair allowlist (as of 2026-08-12)
These exist on purpose. Any dedup pass that flags them is wrong.

| Records | Why both exist |
|---|---|
| **1142 + 1143** Schön *Reflective Practitioner* | 1142 is born-digital and reads better but its index page numbers drift progressively (+9 → +48) and are unusable. 1143 is the 1991 Ashgate scan with a constant +12 offset — the citation-safe copy. |
| **1126 + 1127** Thompson *Manufacturing Processes for Design Professionals* | Deliberate quality pair retained earlier in the session. |
| **2 + 131** Booth et al. *The Craft of Research* | Genuinely different editions: 4th (2016) and 5th (2024). |
| **1162 + 1163 + 1164** *Artists' Pigments* Vols 1/2/3 | Different volumes of one series, not copies. |
| **1171 + 1172** NASA RP-1024 Vols I/II | Vol I is designer guidance, Vol II is the data tables. Different books. |
| **665 + 1106** Goldfinger | *Human Anatomy for Artists* vs *Animal Anatomy for Artists*. |
| **1109 vs any human Bammes** | 1109 is the ANIMAL volume. A human-figure Bammes is a different book, not a duplicate. |

**General rule:** same title + different author (Lang vs Artin *Algebra*) and
textbook + solutions-manual pairs are **not** duplicates. Report, never delete.

---

## Convergence strategy (order matters)

1. **Detect everything first, write nothing.** All classes run read-only; violations
   collected into one report.
2. **Pre-flight before any write:** `pgrep -x calibre` must be empty, and
   `cp metadata.db <scratch>/metadata.db.backup-<ts>`.
   ⚠ A rollback is **not clean**: it reverts the catalogue but not the filesystem, so
   every book added since the backup becomes an A2 orphan. Always re-run the A2 scan
   after any rollback.
3. **Fix in ascending risk order**, one batch per class, verifying after each:
   BLOCKERs (A1, A2, A3, A5, D3) → DEFECTs (A4, B1, B2, B4, D1, D2) →
   HYGIENE in bulk (B3, B5, C1, C2, C3) → REPORT-ONLY to Adam (C4, C5, D4, D5, E1).
4. **Writes go through `calibredb` / `calibre-debug` new_api only** — never direct SQL
   `UPDATE` on metadata.db. Calibre maintains derived state (path layout, search
   caches, link tables) that raw SQL silently desynchronises.
5. **OCR repairs (D1/D2) are non-destructive or they don't happen.** Use
   `ocrmypdf --skip-text --optimize 0`. **Never `--force-ocr`** on a scan — it
   rasterises and re-encodes every page (caught today on Neufert: an RGB JPEG scan at
   283 ppi would have been re-rendered and re-JPEGed). **Never `--jbig2-lossy`** — its
   symbol substitution is the Xerox digit-swap bug, catastrophic in dimension tables.
   Verify after: image dimensions/bpc/ppi unchanged, text-layer chars increased.
6. **Nothing is deleted without positive proof of redundancy** — byte-identical content
   present elsewhere, or an explicit decision from Adam recorded here.
