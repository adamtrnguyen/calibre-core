# Class A (Integrity) verification — a live Calibre library

> **ARCHIVED — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/reports/`
> (deleted; was never under version control).** A point-in-time READ-ONLY verification run,
> preserved for its **method and environment findings** (the OneDrive dataless-placeholder
> mechanics, the A4 false-positive taxonomy) rather than its verdict. The counts are a
> snapshot: 1,094 books / max id 1174 then, **1,122 / 1206** at archive time. Re-run against
> the shipped package rather than trusting the PASS/FAIL table below.

**Date:** 2026-08-12 · **Library:** `~/Calibre Library` · **Mode:** READ-ONLY
**Scope:** A1–A5 of `calibre_target_state.md`. Classes B/C/D/E not checked.
**Books:** 1,094 rows · max id 1,174 · 1,116 format rows · 35.0 GB logical

## Verdict

| ID | Rule | Severity | Violations | Status |
|---|---|---|---|---|
| A1 | every row has ≥1 format; every format file exists | BLOCKER | **0** | PASS |
| A2 | every dir holding a book file has a DB row | BLOCKER | **1** (the known one, re-diagnosed) | FAIL |
| A3 | no file referenced by two records | BLOCKER | **0** | PASS |
| A4 | on-disk path matches current title/author | DEFECT | **1** (case-only, same record as A2) | FAIL |
| A5 | no zero-byte / stub files | BLOCKER | **0 found**, coverage partial (see A5) | PASS (qualified) |

**Net: one real defect, one record (id 66), and it is the pre-existing one the spec
already named.** Everything else in Class A is clean. There are only **10 A4
false positives** to discard, documented below.

---

## Environment finding that shaped the method (read this first)

`~/Calibre Library` is a **symlink** into OneDrive:

```
lrwxr-xr-x  ~/Calibre Library -> ~/Library/CloudStorage/OneDrive-Personal/Calibre Library
```

**989 of 1,116 format files are dataless OneDrive placeholders** (`st_blocks == 0`
while `st_size` reports the true size). Opening one forces a full download.

```bash
find -L "$HOME/Calibre Library" -maxdepth 3 -name '*.pdf' | head -3 \
  | while read -r f; do stat -f "blocks=%b size=%z %N" "$f"; done
# blocks=0 size=101170480 .../Artists' Master Series_ Color and Light (93)/....pdf
```

Consequences, both of which are load-bearing:

1. My first script read the first 1 KB of every PDF to check magic bytes. That
   **hydrates the whole file**, so it was silently downloading the library and was
   killed at 600 s. The rewrite reads magic bytes only for files already local or
   under 64 KB. This is why A5's PDF-header coverage is partial and stated, not
   claimed as a pass.
2. `du -sh` is meaningless here — it reports allocated blocks (~0 for placeholders).
   Every byte figure in this report comes from `st_size`, never `du`.

`os.path.exists` / `os.stat` work on placeholders without hydrating, so A1, A2, A3
and the A5 size test are complete and unaffected.

---

## A1 — every row has ≥1 format, every format path exists · BLOCKER · **0 violations**

- 1,094 rows, **0** with no format row.
- 1,116 format rows, **0** whose file is missing from disk.
- **0** stat or read errors.

Format breakdown (all present):

| Format | Rows | Dataless |
|---|---|---|
| PDF | 1,081 | 847 |
| EPUB | 30 | 18 |
| DJVU | 2 | 1 |
| MOBI | 1 | 1 |
| CBZ | 1 | 0 |
| CBR | 1 | 0 |

22 records carry two formats (EPUB+PDF mostly; 41 is MOBI+PDF; 434 and 939 are
DJVU+PDF) — all files present.

**Caveat, stated rather than buried:** `os.path.exists` on APFS is
case-insensitive, so it returns `True` for `John Montague/...` even though the
directory on disk is `JOHN MONTAGUE/...`. A1 therefore passes *on this machine*.
On a case-sensitive filesystem the id-66 file would be unreachable and A1 would
report 1 missing file. See A2.

---

## A2 — no orphan directories · BLOCKER · **1 violation**

1,094 book directories on disk, 1,094 `books.path` values, **one** name that does
not string-match. It is the violation the spec predicted — but it is **not** what
the spec assumed it was, so the diagnosis matters more than the count.

| Disk directory | DB row | Book file | Bytes |
|---|---|---|---|
| `JOHN MONTAGUE/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)` | **row 66 exists** | `Basic Perspective Drawing_ A Visual Approa - John Montague.pdf` | 161,780,445 |

### It is not a fileless orphan — it is a case mismatch

```
books.path (id 66) : John Montague/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)
on disk            : JOHN MONTAGUE/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)
authors.name       : John Montague          (author id 110)
books.author_sort  : MONTAGUE, JOHN
```

Only the **author directory** differs, and only in case. There is exactly one such
directory on disk (`ls | grep -i montague` → `JOHN MONTAGUE`); the lowercase form
is not a second directory, it is APFS resolving the same inode case-insensitively:

```bash
ls -d "$HOME/Calibre Library/JOHN MONTAGUE" "$HOME/Calibre Library/John Montague"
# both resolve — case-insensitive volume, one real directory named JOHN MONTAGUE
```

So on this Mac the book is **fully functional**: visible in the GUI, listed by
`calibredb`, its file readable, its UUID resolvable. The spec's stated symptom
("invisible to the GUI and to `calibredb list`") does **not** apply here — that
symptom belongs to a genuine fileless orphan, which this is not.

**Root cause (inferred, not observed):** `author_sort` is `MONTAGUE, JOHN`, i.e.
the record was originally added with an all-caps author name and the directory was
created then. The author was later cleaned to `John Montague`, but Calibre's
directory rename is a no-op for a case-only change on a case-insensitive volume,
so the folder kept its old casing while `books.path` was rewritten. Marked as
inference: I read the values, not the history.

**Why it still needs fixing (this is the real risk):** the defect is latent, not
harmless. On any **case-sensitive** target the file becomes unreachable and this
converts from cosmetic to BLOCKER — an rsync to the NAS or an HPC scratch dir, a
case-sensitive APFS volume, or any Linux consumer. Same for anything that compares
`books.path` as a string rather than opening the file.

### Everything else A2 checks — clean

- **0** book directories on disk without a DB row (besides the above).
- **0** `books.path` values with no directory on disk.
- **0** book directories containing `metadata.opf`/`cover.jpg` but no book file
  (i.e. no half-deleted remnants).

### Two non-violations worth recording

**6 empty author directories** — hold no book file, so not A2 violations under the
rule as written. Calibre prunes these during library maintenance.

```
Yaodong Yu Papers · Annie Duke · Eric Ries · Alberto Savoia · Seth Godin
Copi, Cohen, Platt, Hurley, Hulley, Heuer, Cooper, Gauch
```

**`.caltrash`** — 33 book files, **0.87 GB** logical, under `b/<id>/`. Correctly
excluded: this is Calibre's own trash for deleted records, auto-purged. Verified
**none** of the trashed ids is still a live `books` row, so nothing here is an
orphan or an A1 dangler. (Includes `b/834/Good Reasoning Matters!...pdf` — the
Groarke duplicate from the CLAUDE.md incident note. Consistent.)

### Proposed fix

Make DB and disk agree on `John Montague`. **Automatable, but it is a genuine
write and needs the standard pre-flight.**

- Preferred: let Calibre regenerate the path by re-setting the author to its own
  current value, so `construct_path_name` runs and Calibre performs the rename —
  `calibredb set_metadata 66 -f "authors:John Montague"` (or the `calibre-debug`
  new_api equivalent). Never raw SQL: `books.path` is derived state.
- ⚠ **A plain `mv "JOHN MONTAGUE" "John Montague"` may silently no-op** on this
  case-insensitive volume. If a filesystem rename is used at all it must go via a
  temporary name (`JOHN MONTAGUE` → `tmp.montague` → `John Montague`). This is the
  one step where a careless fix does nothing while appearing to succeed.
- Pre-flight per spec §2: `pgrep -x calibre` empty, `metadata.db` backed up.
- Verify by re-running the A2 walk below and confirming `orphans=0`.
- No human decision needed on the target value — the author record is unambiguous.
- Zotero note: `~/Calibre Library/CLAUDE.md` line 142 warns that Zotero
  linked-file attachments break when Calibre reorganizes directories. For a
  **case-only** rename on APFS the stored path string still resolves either way, so
  this specific fix should not break the link — but re-check book 66's Zotero
  attachment afterwards rather than assuming.

---

## A3 — no file referenced by two records · BLOCKER · **0 violations**

Checked three independent ways, all negative:

1. **Path collision (Python, resolved):** grouped all 1,116 format paths by
   `os.path.normcase(os.path.realpath(...))` → **0** paths owned by more than one
   book id.
2. **Path collision (SQL, independent of the script):**
   ```sql
   SELECT b.path||'/'||d.name||'.'||lower(d.format) AS f, COUNT(DISTINCT d.book) c
   FROM data d JOIN books b ON b.id=d.book GROUP BY f HAVING c>1;
   ```
   → empty. Also `SELECT path, COUNT(*) FROM books GROUP BY path HAVING COUNT(*)>1`
   → empty.
3. **Hardlink sharing:** stat'd all 1,116 files; **0** with `st_nlink > 1` shared
   across records. No two records point at one inode.

Note this tests *same file*, per A3 as written. Byte-identical **copies** in
separate directories are E1 (duplicates), not A3, and are out of my scope.

**Proposed fix:** none required.

---

## A4 — on-disk path matches current title/author · DEFECT · **1 violation** (case-only)

### Method: I used Calibre's own path algorithm, not an approximation

My first pass normalized with `unicodedata.NFKD` and flagged 10 records. **All 10
were false positives.** Calibre's `ascii_filename` performs full transliteration
that NFKD cannot do: `陶哲轩` → `Tao Zhe Xuan`, `®` → `(r)`, `Ø` → `O`, `ł` → `l`,
`ちょっと` → `tiyotuto`.

So I re-ran under `calibre-debug`, importing
`calibre.utils.filenames.ascii_filename` and porting
`calibre.db.backend.DB.construct_path_name` verbatim (`PATH_LIMIT = 100`,
confirmed at runtime), then compared the computed path against **both**
`books.path` **and** the real case-preserving on-disk name.

```
PATH_LIMIT = 100
exact match            : 1093
case-only differences  : 1      (id 66)
genuine drift          : 0
```

**This is how I distinguished truncation/substitution from real drift:** I did not
eyeball it or apply a similarity threshold. Re-running with Calibre's actual
function made all 10 suspects match *exactly*, character for character. A record
either reproduces Calibre's own output or it does not.

### The one violation

| id | Field | Value |
|---|---|---|
| 66 | `books.path` | `John Montague/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)` |
| | Calibre would generate | `John Montague/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)` |
| | actually on disk | `JOHN MONTAGUE/Basic Perspective Drawing_ A Visual Approach (6th Edition) (66)` |

`books.path` is **correct**; the filesystem is stale. Same record as A2 — one
defect, two invariants.

### The 10 false positives — do NOT report these as violations

Every one is the same author and the same work, romanized by Calibre. All match
exactly under Calibre's real algorithm.

| id | On-disk (Calibre's transliteration) | DB value | Why it is not a violation |
|---|---|---|---|
| 25 | `Tao Zhe Xuan Shi Fen Xi  (Real Analysis...)` | `陶哲轩实分析 (Real Analysis, 3rd Edition, Chinese)` | Hanzi → pinyin |
| 185 | `UNIX(r) and Linux(r) System Administration Handbook` | `UNIX® and Linux® ...` | `®` → `(r)` |
| 246 | `Arknights Official Artworks Vol 1 Ming Ri Fang Zhou...` | `... 明日方舟官方美术设定集Vol 1 鹰角网络` | Hanzi → pinyin |
| 265 | `The Adobe(r) Photoshop(r) Lightroom Classic Book` | `The Adobe® Photoshop® ...` | `®` → `(r)` |
| 540 | `Oystein Linnebo` | `Øystein Linnebo` | `Ø` → `O` |
| 803 | `Ren Ti Jie Gou Yu Dong Tai Hui Zhi...` (truncated at 100) | `人体结构与动态绘制高效练习法 (...)` | pinyin + `PATH_LIMIT` truncation |
| 886 | `Pawel Lupkowski` | `Paweł Łupkowski` | `ł`/`Ł` → `l`/`L` |
| 921 | `Hu Chuan You Qian  (Yuken Kogawa)` / `Xin Ban  animesiyon...` | `湖川友謙 (Yuken Kogawa)` / `新版 アニメーション作画法 (...)` | kanji→pinyin, kana→romaji |
| 922 | `saidoranti (Side Ranch)` / `tiyotutodokidokisuru...` | `サイドランチ (Side Ranch)` / `ちょっとドキドキ...` | katakana → romaji |
| 923 | `ParyigaQuan Li deJiao eru[Fa ] noMiao kiFang  (How to Draw Hair)` | `Paryiが全力で教える「髪」の描き方 (How to Draw Hair)` | mixed kana/kanji → romaji |

**Uncertainty: none.** This is not a "could not reliably tell" case — the
authoritative comparison is exact-match against Calibre's own function, and it
returned 1093 exact / 1 case-only / 0 drift.

**Proposed fix:** same single fix as A2. Safely automatable, one record.

---

## A5 — no zero-byte or stub files · BLOCKER · **0 violations found; coverage stated**

### Size test — complete, 0 violations

- **0 of 1,116** files below 10 KB.
- **0** stat/read errors; every file returns a size.
- Smallest files in the entire library, showing the margin is not marginal:

| Bytes | id | Format | Directory |
|---|---|---|---|
| 55,531 | 1 | EPUB | `Calibre Quick Start Guide (1)` |
| 105,816 | 163 | PDF | `How to Read a Paper (163)` |
| 169,577 | 894 | PDF | `Creative Hypothesis Generating in Psychology... (894)` |
| 267,209 | 239 | PDF | `New Paradigms in the Psychology of Reasoning (239)` |
| 289,655 | 137 | PDF | `The Art of Controversy (137)` |

The smallest PDF is **105 KB — 926× the ~114-byte HTTP 429 error body** A5 exists
to catch. Nothing sits near the threshold, so the size test is not a close call.

### PDF magic-byte test — partial, and I am reporting it as partial

- **121 of 1,081 PDFs verified** to start with `%PDF`. **All 121 pass.**
- **960 PDFs not verified.**

**Why, and the actual number:** those 960 are dataless OneDrive placeholders.
Reading even the first byte forces a full download. Verifying all of them would
pull **25.6 GB**. I judged that unjustified because the failure mode A5 names is
size-detectable, and the size test came back clean with a 926× margin.

**This is a coverage gap, not a pass.** What remains theoretically undetected is a
PDF larger than 10 KB whose header is not `%PDF` — e.g. a >10 KB HTML error page
saved with a `.pdf` extension. I have no evidence of one, and no evidence against.

**Proposed fix / how to close the gap:** do **not** hydrate 25.6 GB for this alone
— that is a human call on bandwidth and OneDrive quota, and the expected yield is
low. Instead **piggyback**: the D1/D2 text-layer checks must hydrate files anyway,
so have that pass assert `%PDF` on each file it opens. Cost then rounds to zero.
Needs a human decision only in the sense of "don't do the wholesale download".

---

## Out-of-scope observation for the D-class verifier — NOT an A violation

**148 of 1,116 format rows have `data.uncompressed_size != ` the on-disk size**
(146 PDF, 2 EPUB): 102 disk-smaller, 46 disk-larger. The remaining 968 match
exactly, which establishes that this field normally *does* equal file size here —
so the 148 are files changed on disk after Calibre recorded them.

I sampled the three largest shortfalls with `pdfinfo` to separate "stale record"
from "truncated file", because a truncated file would be a D3 BLOCKER:

| id | Title | DB size | Disk size | Δ | `pdfinfo` |
|---|---|---|---|---|---|
| 489 | Computational Optimal Transport | 42,429,657 | 30,178,556 | −12,251,101 | 209 pp, pdfTeX-1.40.17, not encrypted — parses fine |
| 547 | Not Born Yesterday | 6,675,479 | 2,484,109 | −4,191,370 | 384 pp, iTextSharp 5.1.3 — parses fine |
| 587 | Mathematics and Plausible Reasoning, Vol 1 | 15,002,824 | 12,034,274 | −2,968,550 | 298 pp, PDFium — parses fine |

All three parse with plausible page counts, so **these are stale DB size records
from in-place reprocessing or file swaps, not truncation.** The differing
Producers (PDFium, iTextSharp) suggest the files were re-saved or replaced with
different copies after import.

Other notable shortfalls, unsampled: 254 (−2.88 MB), 588 (−2.85 MB), 485
(−1.85 MB), 253 (−1.67 MB), 740 (−1.53 MB), 255 (−1.38 MB), 359 (−0.83 MB), 534
(−0.83 MB), 507, 274, 89, 634. Largest overages: 330 (+989 KB), 53 (+487 KB), 603
(+441 KB), 806 (+309 KB).

**Handing this to the D verifier** because D3 says "cross-check size against any
recorded source size" — 145 files remain unsampled. Fix, if wanted, is to
re-register the format so Calibre refreshes the size; not an A-class action.

## Second out-of-scope note

`~/Calibre Library/CLAUDE.md` is **stale**. It states "**94 books** as of
Feb 2026" (actual: 1,094) and documents Title-Case tag categories (`Mathematics`,
`Color Theory`, `Figure Drawing`) rather than the lowercase-hyphen convention the
target spec and the `calibre` skill require. Relevant to Class C; flagging it
because the library's own doc outranks second-hand descriptions, so it should not
be left describing a taxonomy the library no longer uses.

---

## Reproduction — exact commands

All read-only. DB always opened `mode=ro`.

### Scripts (in this scratchpad)

| File | Purpose |
|---|---|
| `check_A.py` | A1, A2, A3, A5 + first-pass A4. Writes `check_A_raw.json`. |
| `a4_calibre.py` | Authoritative A4 using Calibre's own slug algorithm. Writes `a4_calibre.json`. |

```bash
SP=<session scratchpad>          # where check_A.py / a4_calibre.py were written

# A1 / A2 / A3 / A5  (~20 s; does NOT hydrate placeholders)
python3 $SP/check_A.py
# A1 done: no-format=0 missing=0 of 1116 format rows
# A2 done: disk_bookdirs=1094 db_dirs=1094 orphans=1 db_dirs_missing=0
# A3 done: shared_path=0 shared_inode=0
# A4 done: flagged=10 unparseable=0          <-- 10 false positives, superseded below
# A5 done: small=0 badpdf=0 err=0 sizemismatch=148 magic_ok=121 magic_skipped=960 local=127 dataless=989

# A4 authoritative — MUST run under calibre-debug for the real ascii_filename
/opt/homebrew/bin/calibre-debug -e $SP/a4_calibre.py
# PATH_LIMIT = 100
# exact match: 1093
# case-only differences: 1
# genuine drift: 0
```

### One-liners used

```bash
LIB="$HOME/Calibre Library"       # or "$CALIBRE_LIBRARY"

# confirm the id-66 case mismatch
ls "$LIB" | grep -i montague          # -> JOHN MONTAGUE
sqlite3 "file:$LIB/metadata.db?mode=ro" \
  "SELECT id,title,author_sort,path FROM books WHERE id=66;"
sqlite3 "file:$LIB/metadata.db?mode=ro" \
  "SELECT a.id,a.name,a.sort FROM authors a
   JOIN books_authors_link l ON l.author=a.id WHERE l.book=66;"

# A3, independent of the Python script
sqlite3 "file:$LIB/metadata.db?mode=ro" \
  "SELECT b.path||'/'||d.name||'.'||lower(d.format) AS f, COUNT(DISTINCT d.book) c,
          GROUP_CONCAT(DISTINCT d.book) ids
   FROM data d JOIN books b ON b.id=d.book GROUP BY f HAVING c>1;"
sqlite3 "file:$LIB/metadata.db?mode=ro" \
  "SELECT path, COUNT(*) c FROM books GROUP BY path HAVING c>1;"

# placeholder detection (why magic-byte coverage is partial)
find -L "$LIB" -maxdepth 3 -name '*.pdf' | head -3 \
  | while read -r f; do stat -f "blocks=%b size=%z %N" "$f"; done

# truncation check on the worst size shortfalls (hydrates those files)
pdfinfo "$LIB/Gabriel Peyre/Computational Optimal Transport (489)/Computational Optimal Transport - Gabriel Peyre.pdf"
```

### Nothing was written to the library

No `calibredb add` / `set_metadata` / `remove`; no `UPDATE` on `metadata.db`; no
file moved, deleted or renamed. `metadata.db` opened only as
`file:...?mode=ro`. `a4_calibre.py` runs under `calibre-debug` but only imports
`ascii_filename` and reads the DB read-only — it opens no library for writing.
The scratchpad `books/` download directory was not read or touched.

The only side effect of any kind: the killed first-pass script and the three
`pdfinfo` calls caused OneDrive to hydrate some placeholders locally (127 files
were local at the end vs. fewer at the start). That changes local cache state
only — no library content, no metadata.
