# Calibre invariant checking — design (v2, re-scoped)

> **ARCHIVED — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/reports/`
> (deleted; was never under version control).** This is the design document `calibre-core`
> was built from; the package has since shipped at **v0.2.0** (tag `calibre-core-v0.2.0`).
> Where this document and the shipped code disagree, **the code wins** — read
> `src/calibre_core/` and its tests, and treat the signatures here as historical intent.
> Its own line 13 already says "Nothing is implemented"; that is no longer true.

Design for a **shared `calibre_core` Python package** plus a small checker CLI, replacing
the checkable parts of `~/.claude/skills/calibre/SKILL.md`. Read-only by construction.

**This is v2.** v1 designed a 24-check system with a TOML tag vocabulary. Two course
corrections arrived: (a) the checker belongs in a shared core package that `calibre-mcp`
and `omni-rag` both import, because the real problem is fragmentation; (b) an adversarial
run refuted much of the requirements document, so the scope drops to five components and
the vocabulary schema is deleted. Both are folded in below, and I re-measured every
load-bearing new claim against the live library before rewriting.

Nothing is implemented. Code blocks are illustrative signatures.

---

## 0. What I verified, and the two real defects

All figures below were **observed** this session, read-only, against the live
`~/Calibre Library`. Where I independently reproduced the adversarial agent's
numbers I say so; where we differ I give both.

### 0.1 The library is almost clean

| | Value |
|---|---|
| Books / max id | **1,094 / 1,174** |
| Format files on disk | **1,116** |
| Distinct tags | **363** |
| Custom columns | **1** (`zotero`, composite) |

**Six invariants find zero violations: A1, A2, A3, A5a, C2, C3.** Independently
reproduced — no missing format rows, no missing files, no file owned by two records, no
sub-10 KB stub, no tag failing lowercase-hyphen shape, no junk tag (URL / BISAC /
`Calibre` / `General`).

**The library contains exactly two real defects.** Both found, both reproduced:

1. **id 66 — A4 case drift.** `books.path` says `John Montague`; the directory on disk is
   `JOHN MONTAGUE`.
   ```
   A4 case-exact path violations: 1
     id 66  kind=CASE   db: 'John Montague'   disk: 'JOHN MONTAGUE'
     os.path.isdir() -> True      exists() -> True
   ```
   `isdir()` and `exists()` both return **True** on case-insensitive APFS, so every naive
   integrity check passes it. It becomes unreachable after any rsync to a case-sensitive
   target (NAS, HPC scratch, Linux). This is also the spec's supposed "known A2 orphan" —
   it is not an orphan; id 66 has a DB row. The spec misdiagnosed it.

2. **ids 954 + 995 — a genuine duplicate that title matching cannot see.**
   ```
   size=49,737,184  (954, 'Applied Photographic Optics: Lenses and Optical Systems…')
                    (995, 'Applied Photographic Optics')
   both authors=['Sidney F. Ray']   both PDF   both 49,737,184 bytes
   ```
   Identical byte size, identical author, long-form vs short-form title. **Only
   exact-file-size grouping finds it** — title+surname grouping does not, because the
   titles genuinely differ. Not in the spec's allowlist. This vindicates adding size
   grouping to E1.

Defect rate: **1 record with a structural defect out of 1,094 (0.09%)**, plus one duplicate
pair. That reality, not the 24-invariant spec, is what the design should be sized for.

### 0.2 Where the requirements document is wrong

| Spec claim | Measured |
|---|---|
| C1: 11 tagless records | **33** (reproduced) |
| A2: id 66 is a known orphan | **0 orphans**; id 66 has a DB row and is A4 case drift |
| "Most files are dataless placeholders" | **738 of 1,116 (66%) are hydrated** |
| placeholders are "tens of GB" | **10.83 GiB** over 377 book formats (agent: 10.93 GiB — agrees to ~1%, extension-set difference) |

**Design consequence, unchanged from v1 and now doubly supported: never seed a checker
with hardcoded expected-violation lists, and never suppress a finding because the spec
called it "known."** Both of the spec's named examples were wrong in different directions.

### 0.3 Hydration: bimodal, and partial reads are impossible

Ratio of allocated (`st_blocks*512`) to apparent (`st_size`) across all 1,116 formats:

```
{'0.0': 378, '>=0.95': 738}      # no middle bucket
```

**`st_blocks == 0` is an exact classifier for "dataless placeholder"** — no threshold.

| | files | bytes |
|---|---|---|
| Resident (free to read) | **738** | **21.6 GiB** |
| Dataless (reading costs a download) | **377** book formats | **10.83 GiB** |
| Total apparent | 1,116 | 32.5 GiB |

Dataless: mean 29.4 MiB, median 15.3 MiB. **100 files are < 5 MiB and total 249 MiB;
60 files are > 50 MiB and total 6.24 GiB** — 58% of the bytes in 16% of the files.

Tested the "one byte hydrates the whole file" claim on a 10.06 MiB dataless PDF:

```
before: st_blocks=0                      alloc=0
read 4096 bytes in 4.51s                 magic=b'%PDF-1.5'
after:  st_blocks=20616  alloc=10.07 MiB ratio=1.000
```

Confirmed. **There is no cheap partial read** — a 4-byte magic check costs the whole file.
Two consequences: the unit of cost is one whole file, and its size is knowable from
`stat()` for free, so cost is exactly predictable before reading anything. (~2.2 MiB/s on
that one sample; the full dataless set would be ~84 min. Single sample — order of
magnitude only.)

### 0.4 `.caltrash` — a trap for every tree walk

Library root holds, besides author dirs: `.DS_Store`, `.calnotes`, `.caltrash`,
`CLAUDE.md` (known stale), `full-text-search.db`, `metadata.db`,
`metadata_db_prefs_backup.json`.

`.caltrash` = **99 files, 839 MiB apparent, 15 MiB allocated** — deleted books, mostly
dataless. A naive walk finds **33 book files at depth 4** under `.caltrash/b/<id>/`. Left
unexcluded it makes deleted books look like orphans and would hydrate 839 MiB of things
Adam threw away. The exclusion belongs in the core's one tree-walk, not in each consumer.

### 0.5 The detectors that must not be trusted

**B1 (title junk) is semantically wrong, not merely noisy.** ids 2 and 131 are
`The Craft of Research (4th Edition)` and `The Craft of Research, Fifth Edition` — the same
five authors, a legitimate edition pair the allowlist protects. A *correct* B1 strip makes
both `The Craft of Research` with identical authors, **manufacturing exactly the duplicate
E2 exists to suppress**. The edition suffix is load-bearing disambiguation.

And while writing this design I wrote a throwaway strip regex to test that, which produced:

```
after B1 fix: id2='The Craft of Research ition)'   id131='The Craft of Research,ition'
```

It ate `Ed` out of `Edition`. A one-line regex, written deliberately and carefully, in the
course of designing safeguards against exactly this, mangled two titles. That is the
strongest available argument for §11.

**B2 (author inversion) has two independent false-positive mechanisms.** With the spec's
literal word list I get 42 hits, and the mechanism is the **middle initial `A.`** matching
`\ba\b`: `Beverly A. Sanders`, `Susan A. Ambrose`, `Tim A. Wheeler`. The adversarial run
with the title-side heuristic got 109 hits, ~2 real, hitting *Convex Optimization*,
*Graph Theory*, *Radiative Transfer*, *Fluent Python* — i.e. the naming convention of most
maths/CS monographs. Two different rules, two different failure modes, same verdict.
Genuine signal is rare and heterogeneous: id 427 author `The Frank Cho Method` (a real
inversion — it is the subtitle), ids 309/359 `Editors of Vogue Patterns` and
`University of Chicago Press Editorial Staff` (legitimate corporate authors that will
match any title-word rule forever).

**C4 (synonym clustering) does not work at any threshold.** The adversarial run found one
pair library-wide and could not pair any of the spec's own four cited examples; lowering
the threshold yields `comics`~`economics` and `aesthetics`~`mathematics`. I predicted this
independently in v1 — edit distance also pairs `algebra`/`abstract-algebra`,
`linear`/`linear-algebra`, `optics`/`optimization`, none of which are synonyms.

**E1's subtitle stripping destroys series.** Measured directly:

```
SUBTITLE STRIPPED (spec):    11 groups / 32 records
   group of 9: Morpho: Simplified Forms | Joint Forms and Muscular Functions | Fat and Skin Folds | …
   group of 3: John Singer Sargent: The Early Portraits | Venetian Figures | Figures and Landscapes
   group of 3: Hito wo Kakunotte Tanoshii ne: Clothing | Face | Manga Anatomy
   group of 3: Introduction to Logic | …Instructor's Manual | …Solutions Manual
SUBTITLE KEPT (proposed):     4 groups /  8 records
```

Keeping the subtitle takes the review load from 32 records to 8 and eliminates every bogus
series group. Of the 4 surviving groups, 3 are allowlisted intentional pairs (2+131,
1126+1127, 1142+1143) and one is a textbook/instructor-manual pair (31+32) covered by the
spec's general rule. **Effectively zero unexplained title duplicates** — plus the one
size-duplicate from §0.1 that titles miss.

One caution discovered while testing the fix: adding spelled-out ordinals to the strip
list to catch `Fifth Edition` also stripped `First`/`Second` from a real two-volume pair
titled *The First Tutorial …* / *The Second Tutorial …*, creating a fresh false positive.
**Strip a spelled-out ordinal only when immediately followed by `edition`.** That is a
regression test, not a footnote.

### 0.6 Tags: 165 singletons is where the junk is

```
tags total 363 · used exactly once: 165 · zero uses: 0
```

165 singletons reproduces the adversarial figure exactly. Zero unused tags — so drift is
strictly one-directional: the doc is behind, never ahead. Among the singletons: **18
obvious author-name tags** (`alessandra-tanesini`, `j-adam-carter`,
`javier-gonz-lez-de-prado-salas`, `finnur-dells-n`, …), one
`argumentation-rationality-testimony-evidence-inference`, one `duplicate-copy`, and a
short-tag band that mixes real junk with legitimate acronyms:

```
5  basic  bull  frame  length  lens  line  linear  medium  motif  raging  rule
shot  thirds  volume  zoom          <- junk
dpo  llms  rlhf  rlvr  slam  uav  ux  vim  visdev  survey  essays   <- legitimate or arguable
color  vision                                                       <- near-synonyms
```

That mixture is why the singleton report must be a **review list, never an auto-delete**.

### 0.7 Tooling and a free-but-empty data source

Present: `pdftotext`, `pdfinfo`, `pdfimages`, `pdffonts`, `ebook-meta`, `calibredb`,
`calibre-debug`, `ocrmypdf`, `sqlite3`. `calibredb add_custom_column <label> <name>
<datatype>` exists with `--is-multiple` for `text` — so §12's `#dupok` column is
mechanically available.

`full-text-search.db` carries `books_text(book, format, format_size, text_size, …)` —
`text_size` is exactly what a text-layer check wants, free. But:

```
books_text rows: 3 / 1094 books (0.3%)     dirtied_formats queued: 744
$ calibredb fts_index status  ->  FTS Indexing is disabled
```

Unusable today. `calibredb fts_index enable|status|reindex` exists with
`--indexing-speed {fast,slow}`, so it could be populated — but it costs the same 10.83 GiB,
it is a write to library state, and **`text_size` is a whole-book total, which cannot
answer the partial-coverage question** (the spec's own confirmed case: 1.17 M chars hiding
200 textless leading pages). Keep it outside the tool; read it opportunistically if rows
appear. *(Caution: one indexed row has `text_size = 1,164,542` ≈ the "1.17 M" the spec
cites for id 1158. It is id 3, not id 1158. Coincidence, not corroboration.)*

---

## 1. The shared core package

### 1.1 The problem being solved is fragmentation

Per the codebase survey: **three mutually inconsistent title normalisers across two
repos**, one of which (`omni-rag/scripts/calibre_metadata_audit.py:112`
`normalized_title()`) strips `[^a-z0-9]` with **no CJK ranges**, collapsing every CJK title
to the empty string — the exact bug `calibre-mcp` comments twice as having caused. Five
copy-pasted `sqlite3.connect(f"file:…?mode=ro", uri=True)` sites with no shared helper,
plus `omni-rag/scripts/reconcile_library.py:29` opening the same `metadata.db`
**read-write**, SELECT-only by convention. Six invariants already implemented in the wrong
place. Two different access paths (direct sqlite vs `calibredb` subprocess).

So the deliverable is not "a checker." It is **one package that owns the shared substrate**,
with the checker as its first real consumer.

### 1.2 What core owns, and what it must not

```
calibre_core/                     # stdlib only (see §1.7)
  library.py      Library         -- the single read-only access chokepoint
  model.py        Record, FormatFile, TagUsage, FileState
  normalize.py    fold, title_key, author_surname, tag_slug, CJK_RANGES
  report.py       Report, Finding, Status, Severity, exit codes
  registry.py     CheckSpec, Check protocol, REGISTRY, @register
  checks/         one module per check
  files.py        tree walk (exclusions), stat classifier, measurement cache
  fixtures/schema.sql, testing.py
```

**Dependency direction is one-way and absolute:**

```
calibre-mcp  ─┐
omni-rag     ─┼──> calibre_core ──> stdlib only
calinv (CLI) ─┘
```

`calibre_core` **never** imports from `calibre-mcp` or `omni-rag`. It contains no MCP
types, no FastMCP decorators, no omni-rag config, no CLI argument parsing. Enforced by a
test that walks core's imports and fails on anything outside stdlib + `calibre_core`.

### 1.3 The access-path decision: sqlite for reads, `calibredb` for writes

Core exposes **exactly one** way to read and **no** way to write.

**Reads: direct read-only sqlite.** Reasons: (a) speed — every measurement in this
document ran in well under a second over 1,094 records, whereas `calibredb list
--for-machine` spawns a Python interpreter and serialises the catalogue; (b) expressive
power — the case-exact path audit and the exact-size duplicate grouping need joins and
per-format `uncompressed_size` that `calibredb list` cannot express; (c) it is already what
`calibre-mcp` does, so consolidating there is the smaller migration. The `omni-rag` scripts
that reach the DB via `calibredb` subprocess should switch to importing core.

**Writes: `calibredb` / `calibre-debug` new_api subprocess, and not from core.** Calibre
maintains derived state (path layout, search caches, link tables) that raw SQL silently
desynchronises. Core has no write path at all — see §11.

Critically, this **deletes the `reconcile_library.py:29` hazard**: a bare read-write
`sqlite3.connect()` on the live catalogue, safe only by convention, is one stray statement
away from corrupting `metadata.db` while Calibre is running. Core's opener is read-only by
construction and there is no alternative to reach for.

```python
class Library:
    def __init__(self, path: Path, *, snapshot: bool = True): ...
```

**`snapshot=True` copies `metadata.db` (1.8 MB) to a temp file and reads that.** Two
payoffs: all checks in one run see a single consistent state even if Calibre or omni-rag
writes mid-run, and it makes "same library in → same report out" (§2) actually true rather
than aspirational. Cheap enough to be the default.

### 1.4 ONE normaliser — the most important export

The three existing copies differ for partly *legitimate* reasons: fuzzy search wants
aggressive folding, dedup grouping wants edition-word stripping, display wants minimal
change. So the fix is not one function but **one module with named, documented, tested
functions sharing one character-class constant**:

```python
# calibre_core/normalize.py
CJK_RANGES = "぀-ヿ㐀-䶿一-鿿가-힯"   # kana, CJK-A, CJK, hangul. NEVER remove. See test_cjk.

def fold(s: str) -> str:
    """NFKD + strip combining marks + casefold. Preserves CJK. For fuzzy search (calfuzz)."""

def title_key(s: str) -> str:
    """Grouping key for duplicate detection.
    KEEPS the subtitle -- stripping it collapses real series (measured: 32 records -> 8).
    Strips a parenthetical/bracketed edition marker, and a spelled-out ordinal ONLY when
    immediately followed by 'edition' (bare 'First'/'Second' are real title words).
    Preserves CJK ranges; returns '' for titles that normalise to nothing, which callers
    must treat as a metadata problem to report, never as a bucket key."""

def author_surname(s: str) -> str:
    """Last whitespace token of the first author, folded. Middle initials are NOT
    significant -- see the B2 false-positive mechanism, §0.5."""

def tag_slug(s: str) -> str | None:
    """lowercase-hyphen, '&' -> 'and'. None for junk (URL, BISAC, Calibre, General)."""
```

Four properties that are regression tests, not prose:

1. `title_key` **keeps subtitles** (measured: 11 groups/32 records → 4 groups/8).
2. Spelled-out ordinals strip **only before `edition`** (measured false positive:
   *The First/Second Tutorial …*).
3. Every function **preserves `CJK_RANGES`** — the live bug in
   `calibre_metadata_audit.py:112` is a test case with a real Chinese title as input.
4. `title_key('')` and empty results are **reported, never bucketed** — the `if not k`
   guard the skill already documents.

`fold` and `title_key` are deliberately *different functions*, so the two existing
`calibre-mcp` normalisers (`matching.py:38` `norm()` and `matching.py:134` `key()`) each
map onto one of them rather than being forced to merge. The bug was never that there were
two; it was that they were undocumented, untested, and a third copy silently dropped CJK.

### 1.5 The result envelope: extend `check_index.py`, don't invent a third convention

`omni-rag/scripts/check_index.py` is the precedent: a `Report` class with
`add(name, status, detail, items)`, PASS/WARN/FAIL where WARN alone does not fail the run,
`--json` plus exit 0/1, thresholds as module constants explicitly not computed at runtime,
and a docstring promising "same index in → same report out." Core adopts that shape and
extends it in exactly two places.

```python
class Status(Enum):
    PASS = "pass"; WARN = "warn"; FAIL = "fail"
    INFO = "info"          # NEW: report-only findings, never affect exit
    PARTIAL = "partial"    # NEW: the check ran but did not see everything
```

Spec severity maps onto this without a third vocabulary:

| Spec severity | Status | Affects exit? |
|---|---|---|
| BLOCKER, DEFECT | `FAIL` | yes |
| HYGIENE | `WARN` | no (matching the precedent) |
| REPORT-ONLY | `INFO` | no |
| — | `PARTIAL` | yes, via exit 2 |

`PARTIAL` is the one genuinely new state, and it is needed because `check_index.py` has no
sampling — it never had to express "we did not look at everything." This checker does
(§3).

Thresholds as module constants, copying that discipline explicitly:

```python
STUB_MAX_BYTES        = 10 * 1024
TEXT_CHARS_PER_100PP  = 500          # below => image-only
SAMPLE_OFFSETS        = (0.10, 0.35, 0.60, 0.85)
PPI_MARGINAL          = 150
SINGLETON_TAG_COUNT   = 1
```

### 1.6 Public API surface — what consumers import

```python
from calibre_core import Library, Record, FormatFile, FileState
from calibre_core.normalize import fold, title_key, author_surname, tag_slug, CJK_RANGES
from calibre_core.report import Report, Finding, Status, Severity, ExitCode
from calibre_core.registry import REGISTRY, CheckSpec, register, select
from calibre_core.files import walk_library, classify, MeasurementCache
from calibre_core.duplicates import group_by_title, group_by_size, group_by_isbn
from calibre_core.preflight import preflight            # §5
```

Per consumer:

- **`calibre-mcp`** — MCP tools (`search_books`, `find_duplicates`, `find_orphaned_files`,
  `library_stats`, `list_tags`) become thin adapters over `Library` + `duplicates` +
  `walk_library`. `calfuzz` keeps its scoring blend but calls `normalize.fold` instead of
  its local `norm()`. Its five ad-hoc `sqlite3.connect` sites collapse to one `Library`.
- **`omni-rag`** — `calibre_metadata_audit.py` **deletes** its six local invariants and its
  duplicate grouping and imports the core checks; `reconcile_library.py` drops its
  read-write connection for `Library`. Its ISBN-10/13 checksum validation is *better* than
  anything in the spec and should **move into core**, not be discarded — it is the one piece
  of existing work that adds capability rather than duplicating it.
- **`calinv`** (new CLI) — argument parsing, output formatting, the `apply` step. Owns no
  library logic.

### 1.7 How core becomes testable, given that nothing is today

The survey found no tests, no fixtures, no schema to build one from, and one dead DI
parameter (`load_books(db=...)` never called with an argument); the only working seam is
the `CALIBRE_LIBRARY` env var, which works because `library_path()` reads it at call time.
Four moves:

1. **Constructor injection, not env var.** `Library(path)` takes the path explicitly. Env
   var resolution lives in the CLI and MCP layers, never in core. This kills the dead-DI
   problem by removing the need for DI: there is nothing to inject because nothing is
   global.
2. **Vendor the schema.** There is no schema doc — so derive one: `sqlite3 metadata.db
   .schema`, reduce to the tables core reads (`books`, `authors`, `books_authors_link`,
   `tags`, `books_tags_link`, `data`, `identifiers`, `languages`,
   `books_languages_link`, `series`, `books_series_link`, `custom_columns`), and commit it
   as `fixtures/schema.sql`. A generator script regenerates it so it can be refreshed when
   Calibre's schema moves.
3. **`testing.make_fixture_library(tmp_path, records=[...])`** builds a real minimal
   Calibre-schema SQLite DB *and* a real directory tree, so checks run end-to-end on
   something with no OneDrive and no 32 GiB. Fixtures must cover the cases this session
   surfaced: a `JOHN MONTAGUE` case-drift record, a byte-identical size pair, a CJK title,
   a `.caltrash` entry, a 9-volume series, an edition pair.
4. **An injectable stat provider** for the file-state classifier, since a dataless
   placeholder cannot be created without OneDrive. `classify(path, statter=os.stat)` lets a
   test simulate `st_blocks=0`. This is the only seam Class D testing needs, and §0.3's
   bimodality is what makes a fake this simple sufficient.

Then the precedent's determinism promise becomes a real test: **golden-file comparison of
`--json` output against a committed fixture**, with `run_id` and timestamps excluded.

---

## 2. Severity and exit semantics

### 2.1 Exit codes: the precedent's 0/1, extended by two

| Code | Meaning |
|---|---|
| **0** | No `FAIL`, and every selected check completed. |
| **1** | At least one `FAIL`. (`WARN`/`INFO` alone never reach here.) |
| **2** | **Inconclusive** — a selected check is `PARTIAL`, or `--require-coverage` unmet. |
| **3** | Tool / precondition error: DB unreadable, `apply` with the GUI open. |

0/1 keeps the `check_index.py` contract intact for existing consumers. 2 and 3 are
additions that the sampling problem forces. A bitmask was considered and rejected: it makes
every consumer learn an encoding to answer "did anything break," and per-severity detail
belongs in the JSON where it needs no decoding.

**Exit 2 is what stops a partial run reading as a clean bill of health.** It is a distinct
non-zero code, so `set -e` and `if calinv check` both trip on it, and a consumer that reads
nothing but `$?` still cannot mistake incomplete for clean.

### 2.2 `verdict` is not derivable from the exit code

```
verdict: "clean"         # every selected check completed AND found nothing
       | "findings"      # every selected check completed, something was found
       | "inconclusive"  # >=1 selected check PARTIAL -- can NEVER be "clean"
```

`clean` is gated on completeness at the data-model level. Exit 0 only means "no FAIL."
Those come apart exactly when coverage is partial, and keeping them separate is what stops
"exit 0" being quoted as "the library is fine."

### 2.3 Avoiding an always-failing default

If every check ran by default, the file-content checks could never reach full coverage at
a zero hydration budget, so the default invocation would *always* exit 2 — and an
always-failing default gets ignored, destroying the signal exit 2 exists to send.

Each check declares a coverage policy, and content checks are opt-in:

```python
class CoveragePolicy(Enum):
    REQUIRE_COMPLETE = auto()   # DB/stat only. <100% is a tool failure.
    BEST_EFFORT      = auto()   # file-content checks. Coverage always reported.
```

- `calinv check` → DB/stat checks only. All complete, all free. Exit 0 achievable and
  meaningful. Cron-safe.
- `calinv check --content` → adds file-content checks. `inconclusive` + exit 2 unless an
  explicit `--require-coverage` is met. Honest by construction.

### 2.4 Output rules the formatter enforces

Three rules, in the formatter rather than left to each check:

1. **The words `PASS` and `OK` never appear as a bare verdict.** A check that found nothing
   prints its denominator instead.
2. **Every result line carries `found / examined of eligible`:**
   ```
   D1  image-only PDFs ......... 0 found | examined 738 of 1,116 eligible
                                 378 not examined (dataless, budget=0)   PARTIAL
   ```
3. **The summary names the incomplete checks**, in fixed wording, because this is the line
   that gets quoted:
   ```
   INCONCLUSIVE — 8 of 11 checks complete; 3 PARTIAL (D1, D2, D4).
   This is not a clean bill of health.
   ```

Modes: `--format text` (default; coverage block at **top and bottom**, since scrollback
truncates the top and summarisers read the tail), `--json` (machine contract), `--jsonl`,
`--format md`, `--quiet`.

Three contexts: Adam ad hoc reads the text; an agent reads `$?` then `verdict`; cron runs
the free set with `--json --emit-unexamined next.json`.

---

## 3. Check interface and coverage

### 3.1 Layers, so cost is declared rather than discovered

```python
class Layer(Enum):
    DB = auto()          # snapshot metadata.db          free (~ms)
    FS_STAT = auto()     # stat + dir listing, exclusions free (<1s for 1,116)
    FS_CONTENT = auto()  # file bytes                     HYDRATION -- budgeted
    FTS = auto()         # books_text.text_size           free; 0.3% coverage today
```

No network layer: no check in scope needs an online lookup, which keeps the tool offline
and deterministic.

```python
@dataclass(frozen=True)
class CheckSpec:
    id: str; title: str
    severity: Severity
    layers: frozenset[Layer]
    coverage: CoveragePolicy
    fixability: Fixability          # NEVER | SUGGEST | SAFE
    eligible: Callable | None       # e.g. PDF-only
    doc: str

class Check(Protocol):
    spec: CheckSpec
    def run(self, lib: Library, ctx: RunContext) -> Iterator[Finding]: ...
```

**A check whose detection spans two cost layers must be split.** Otherwise the free half is
held hostage by the expensive half. The spec's A5 is the case in point: `stat` for size is
free, but "first bytes are not `%PDF`" costs the entire file on a placeholder (§0.3) — so
A5 as specified is a 10.83 GiB full-library download wearing the label of a cheap
BLOCKER check. Split into A5a (size, free, always 100%) and A5b (magic, budgeted,
best-effort).

### 3.2 Findings carry their evidence

```python
@dataclass(frozen=True)
class Finding:
    check: str
    status: Status
    severity: Severity
    subject: Subject                   # book id | format path | tag | group
    message: str
    evidence: Mapping[str, object]     # REQUIRED -- the measured numbers
    method: str                        # "stat" | "pdftotext" | "fts" | "db" | "listdir"
    confidence: float | None           # heuristic checks only
    suggested_fix: Fix | None          # never applied by `check`
    exception: ExceptionRef | None     # populated when suppressed
```

`evidence` is mandatory and holds numbers, so a finding is auditable without re-running:
`{"pages": 336, "offsets": [34,118,202,286], "chars_per_offset": [12,9,11,8], "threshold": 500}`
— not "text layer looks thin."

`method` records provenance, because a text-layer verdict from `books_text.text_size` (a
whole-book total) is weaker evidence than four sampled offsets, and coverage accounting
must not sum them into one percentage.

**Suppressed findings are emitted, not filtered** — status downgraded, `exception`
populated, counted as "N suppressed." A silently filtered allowlist is how a stale
allowlist hides a regression, and §0.2 shows the spec's allowlist was already 4/7 dead.

### 3.3 Coverage reporting

The requirement — *a partial run can never be mistaken for a clean bill of health* — is now
much cheaper to satisfy than the spec feared, because 66% of files are already resident and
the cache (§6) makes hydration a one-time cost. Six mechanisms, layered:

1. **Coverage is per-check, never global.** One global "87% covered" lets a 100%-covered
   integrity check launder a 12%-covered content check. Every check owns
   `(eligible, examined, by_state, not_examined[reasons])`.
2. **No PASS verdict** — only "0 found in the examined set" (§2.4).
3. **`status` per check** distinguishes `complete` / `partial` / `skipped` / `error`;
   "we didn't look" is structurally different from "we looked and it was fine."
4. **`verdict` can only be `clean` when everything completed** (§2.2).
5. **Exit 2 for inconclusive** (§2.1).
6. **`--emit-unexamined FILE`** writes the exact book ids and format paths *not* examined,
   with reasons — so partial coverage becomes a **work queue** rather than an apology, and
   the next run (or an ARC-side job with those files staged) takes exactly that set.

`measure()` returns `Unavailable(reason)` rather than raising, so a check cannot
accidentally omit a file from its denominator — declining to measure is a value it must
handle.

---

## 4. Scope: five components

The 24-invariant framing is dropped. What ships, in priority order:

| # | Component | Cost | Finds |
|---|---|---|---|
| 1 | **Add-time preflight** | free (staged file is local) | every confirmed real defect, before it enters the library |
| 2 | **Content-measurement cache** | makes hydration one-time | is what makes content checking converge at all |
| 3 | **A4 case-exact path audit** | ~1 s, no file reads | the library's one real structural defect |
| 4 | **E1 with a fixed normaliser** | free | 4 title groups + the 954/995 size duplicate |
| 5 | **Singleton-tag report** | free | 165 tags, where the real tag junk lives |

Retained as free regression tripwires because they cost nothing and currently pass: A1, A2,
A3, A5a, C2, C3, C1 (33 tagless). Their value is not discovery — it is catching a
regression after a batch add or a rollback, which is the spec's own stated need.

---

## 5. Component 1 — add-time preflight (highest value)

### 5.1 Why this is first

Every confirmed real file defect in the requirements document — 336 chars across 336 pages;
200 completely textless leading pages behind a healthy 1.17 M-char total; silently dropped
table numerals — **would have been caught here, for free, before the file entered the
library.** The staged file in `/tmp` is fully local: no placeholder, no hydration, no
OneDrive. The single most expensive problem in the whole design (§0.3) simply does not
exist at add time.

This inverts the spec's framing. The spec treats file quality as a library-wide sweep to be
sampled and budgeted. It is really an **admission check**, and a sweep is only needed for
the backlog that predates it.

### 5.2 What it does

```
calinv preflight /tmp/Book.pdf --title "…" --authors "…" --tags "a,b"
```

| Gate | Method | Status on failure |
|---|---|---|
| Real PDF/EPUB | magic bytes; `pdfinfo` succeeds; EPUB `zipfile.testzip()` | `FAIL` |
| Not a stub / error body | `st_size >= STUB_MAX_BYTES` | `FAIL` — a 114-byte "PDF" is an HTTP error body, not a book |
| Text layer present | `pdftotext` at 4 offsets, `chars/100pp >= 500` | `FAIL` (queue for OCR) |
| Text layer **covers the book** | **per-offset** density, each reported separately | `FAIL` — one near-zero offset beside three healthy ones is the partial-coverage signature |
| Resolution usable | `pdfimages -list` sampled ppi vs `PPI_MARGINAL` | `WARN` — purpose-relative, §13 |
| Table numerals extract | digits present on an operator-named table page | `INFO`/prompt — needs human input, §13 |
| Not already owned — title | `title_key` + `author_surname` against the live DB | `WARN` + show candidates |
| Not already owned — **bytes** | **exact file size** against `data.uncompressed_size` | `WARN` — this is what found 954/995 (§0.1) |
| Tag novelty | each tag ∉ `SELECT name FROM tags` → prompt to confirm or pick an existing tag | `INFO` + prompt |
| `--title`/`--authors` present | both required | `FAIL` — omitting them makes `calibredb add` parse the filename as `Title - Author` while sources name files `Author - Title`, silently inverting the record |

Exit non-zero blocks the add. On success it prints (or `exec`s) the `calibredb add` line
with the validated metadata, so the sanctioned pipeline is the path of least resistance.

The tag-novelty prompt is the entire replacement for the vocabulary schema (§10): it is a
**write-time** question to a human who has the book in front of them, which is the only
moment the answer is cheap and reliable.

---

## 6. Component 2 — the content-measurement cache

Without it, every sweep re-pays full hydration and never finishes; the spec's
"never converges" is a caching problem misdiagnosed as a sampling problem.

**Sidecar SQLite keyed by `(path, size, mtime)`.** Each file hydrates **once ever**,
re-examined only when size or mtime changes. Stores every probe result: magic bytes, page
count, per-offset char counts, ppi samples, `testzip` result, plus `measured_at` and tool
versions (so a `pdftotext` upgrade can invalidate deliberately).

This is what makes coverage monotonically increase across runs — a second run costs zero
for anything already measured **even if OneDrive has since evicted the bytes**, because the
expensive thing was the download and it was paid once. That, not sampling cleverness, is
convergence.

Supporting policy, from §0.3's measurements:

- **Budget in bytes, defaulting to 0.** Because cost is exactly knowable from `stat()`
  before reading, `--estimate` prints the full plan and its byte cost for free.
- **Resident set first, exhaustively** — 738 files / 21.6 GiB, free, **66% of the library
  at zero cost**.
- **Dataless by priority, cheapest-first within priority.** Priority = reference works
  where numerals *are* the content (`anthropometry`, `ergonomics`, `pigments`, `optics`,
  `materials`, `physics`, `mathematics`). Cheapest-first because 100 files fit in 249 MiB
  while 60 files cost 6.24 GiB — sorting by size buys ~2 orders of magnitude more files per
  byte.
- **Never touch the 60 files > 50 MiB** (6.24 GiB, 58% of dataless bytes) unless named:
  `calinv check --content --book 1158`.
- **One file at a time, result written immediately.** No "hydrate the batch then analyse" —
  that is the pattern eviction undoes in flight.

⚠ **Open Question 6 is a prerequisite, not a footnote:** if OneDrive eviction rewrites
`mtime`, the `(path, size, mtime)` key invalidates spuriously and this entire component
silently stops working — re-downloading files it already measured. Test before building:
hydrate, record `mtime`, force eviction, re-`stat`. If eviction touches `mtime`, key on
`size + inode + data.format_hash` instead.

---

## 7. Component 3 — A4 case-exact path audit

Reproduced in §0.1: **1 violation library-wide**, id 66, and it is the library's only real
structural defect.

**Method.** For each `books.path`, walk its components and require each to be present in
`os.listdir(parent)` as an **exact string**. Do not use `isdir()`/`exists()` — both return
`True` here on case-insensitive APFS, which is precisely why this defect is invisible. Do
not use NFKD: Calibre's `ascii_filename` transliterates in ways NFKD cannot
(陶哲轩 → `Tao Zhe Xuan`, `®` → `(r)`, ちょっと → `tiyotuto`), so a full path audit needs
the real function imported under `calibre-debug`. The **case-only** subset needs no Calibre
import at all — it is a pure `os.listdir` comparison, which is why it can ship first and
cheaply.

**Cost:** ~1 second, zero file reads, directory listings only. Belongs in core as
`checks/a4_case.py`, exposed to `calibre-mcp` as a tool.

**When to run:** after every `metadata.db` rollback (a rollback reverts the catalogue but
not the filesystem) and **before every rsync to a case-sensitive target** — NAS, HPC
scratch, Linux — because that is the moment the defect stops being invisible and starts
being data loss.

**Fix:** report only. The repair is the documented round-trip
(`set_metadata` to `Correct Name TMPFIX`, then back, because `set_metadata` no-ops on an
unchanged value) — and it **moves a directory**, which puts it squarely under §11's
prohibition. Adam runs the two commands; the tool prints them.

---

## 8. Component 4 — E1 with a fixed normaliser, report-only forever

Three changes to the spec's detector, each measured:

1. **Keep the subtitle.** 11 groups / 32 records → **4 groups / 8 records**, and every
   bogus series group disappears (9 Morpho volumes, 3 Sargent, 3 Japanese manga anatomy,
   3 *Introduction to Logic* textbook+manuals).
2. **Handle spelled-out edition words** (`Fifth Edition`) — but **only when the ordinal is
   immediately followed by `edition`**, or you create a fresh false positive on
   *The First / Second Tutorial …*.
3. **Add exact-file-size grouping.** One group library-wide, and it is the real duplicate
   title matching cannot see: 954 + 995, both Sidney F. Ray, both 49,737,184 bytes.

Also group by ISBN (2 groups). Of the 4 surviving title groups, 3 are intentional pairs and
1 is a textbook/instructor-manual pair — so the steady-state output is a handful of lines,
which is what makes it worth reading.

**Report-only forever.** Nothing is deleted without positive proof of redundancy. Same
title + different author (Lang vs Artin *Algebra*) and textbook + solutions-manual pairs
are not duplicates.

---

## 9. Component 5 — singleton-tag report

165 tags used exactly once; 0 unused. Report them ranked by suspicion, with the signals
shown rather than a verdict:

- **author-name shape** (18 obvious: `alessandra-tanesini`, `j-adam-carter`, …) — these are
  a systematic import artefact, likely one batch, so grouping by shape makes the batch
  visible;
- **very short** (`5`, `bull`, `basic`, `rule`, `shot`, `thirds`, `frame`, `lens`, `line`,
  `medium`, `motif`, `volume`, `zoom`);
- **over-long compound** (`argumentation-rationality-testimony-evidence-inference`);
- **checker-control leakage** (`duplicate-copy` — §12 replaces it);
- **near-synonym of an established tag** (`color` vs `color-and-light`, `vision` vs
  `visual-perception`).

**Never auto-delete**, because the same band holds legitimate acronyms — `dpo`, `rlhf`,
`rlvr`, `llms`, `slam`, `uav`, `ux`, `vim`, `visdev`. Any rule sharp enough to remove `5`
and `bull` also removes `dpo` and `uav`. This is a review list; the human is the classifier.

---

## 10. The tag vocabulary: why the schema is gone

**Measured:** 363 tags live, ~145–152 documented (my parse of the skill's taxonomy block
gives 145; the survey says 152 — a parse-boundary difference that changes nothing),
**211–218 undocumented (58–60%)**, and **zero documented tags are unused**.

Drift is strictly **one-directional**: the document is behind, never ahead. So encoding the
taxonomy as data does not create a check — it converts a stale document into **a stale
assertion repeated 211 times per run**. A closed vocabulary would report 211 violations on
its first run and be switched off the same day, and Adam added 8 tags in one day, so the
rate of legitimate additions guarantees it stays behind.

**Replacement, in three parts:**

1. **The vocabulary is `SELECT name FROM tags`.** The library *is* the controlled
   vocabulary. There is no second copy to drift.
2. **Novelty is checked at add time** (§5), when a human has the book open and the answer
   is cheap — not at audit time against a list, when it is 211 false accusations.
3. **Singletons are the junk report** (§9), which is where the real problem actually is.

What this gives up, honestly: per-tag aliases, deprecated-in-favour-of pointers, and notes
like the anthropometry-vs-anatomy distinction. Those are **genuine judgment**, and the
brief's own goal was to leave the skill carrying only genuine judgment. They stay in prose,
which is the right home for them. The checkable part — "is this tag new?" and "which tags
are used once?" — moves into code. That is the split the brief asked for; it just turns out
the vocabulary *list* was never the checkable part.

One retained idea: `calinv tags dump --format md` regenerates the skill's taxonomy section
from the live DB, so the prose is **generated output** rather than a hand-maintained second
copy. That is the only durable fix for a 211-tag drift, and it needs no schema.

---

## 11. Writes: read-only, and `title`/`authors` are untouchable

### 11.1 The checker has no write path

Not "read-only by default" — **no write capability in core or in `check`**. The
requirements doc says verifiers are read-only and convergence happens in reviewed,
backed-up batches. A write needs the GUI closed and a fresh backup: properties of a
deliberate maintenance window, not of a check that should be runnable while Adam has
Calibre open — which is exactly when it must not be able to write.

Repair, when wanted, is a **separate** subcommand over a reviewable plan:

```bash
calinv check --emit-plan plan.json     # proposes; writes nothing
calinv plan  plan.json --explain       # human rendering
calinv apply plan.json --dry-run | --commit
```

`apply` refuses to start (exit 3) unless `pgrep -x calibre` is empty and a backup
succeeded; writes only via `calibredb set_metadata` / `calibre-debug` new_api, never SQL
`UPDATE`; and **re-verifies each field's current value against the plan's `from` value**,
skipping loudly on drift. That staleness guard is what makes an offline-reviewed plan safe
to run later.

### 11.2 `--fix` must never touch `title` or `authors`

Four independent reasons, three of them measured this session:

1. **There is no rollback, only forward repair.** A retitle or re-author **moves a
   directory** inside a 32.5 GiB OneDrive tree. The documented recovery is restoring
   `metadata.db` — which reverts *the catalogue but not the filesystem*, so every affected
   book becomes an orphan and the "backup" makes things worse. For every other field a
   backup is a rollback; for these two it is not.
2. **A title↔author swap is not idempotent — so a buggy fix looks like convergence.**
   Applying the swap twice returns to the original, which means a verification pass that
   re-runs the detector reports "no inversion" both when the fix worked *and* when it
   inverted a record that was already correct. The verification step cannot distinguish
   success from a new defect. Combined with B2's ~2-real-in-109 precision, a fix-then-verify
   loop would report clean while having corrupted dozens of correct records.
3. **B1's fix is semantically wrong, not just risky** (§0.5). A correct edition-suffix
   strip on ids 2 and 131 makes both `The Craft of Research` with identical authors,
   manufacturing the duplicate the E2 allowlist exists to suppress. The suffix is
   disambiguation, and the spec's own two rules contradict each other here.
4. **Regexes over titles mangle titles.** My own throwaway strip produced
   `'The Craft of Research ition)'` — it ate `Ed` from `Edition`. Written carefully, while
   designing safeguards against this exact failure.

So: `title` and `authors` are **report-only, permanently**, and `fixability=NEVER` is
enforced in `CheckSpec` for every check touching them — a plan entry naming those fields is
rejected by `apply`, not merely discouraged.

### 11.3 What is actually safe to write

| Field | Fixability | Note |
|---|---|---|
| `tags` — shape normalisation (C2), junk removal (C3) | **SAFE** | Deterministic, closed output alphabet. Both currently have **0 violations**, so the risk surface is empty. |
| `comments` — append a data-quality caveat | **SAFE** | Append-only, no derived state, and the spec asks for it (D5 warnings). |
| `pubdate` — clear the `0101-01-01` sentinel | SUGGEST | Clearing is safe; supplying the right date needs the CIP block. |
| `languages` | **NEVER** | 738 records, and the value **is not determinable from the DB**. Defaulting to `eng` is fabrication — the library visibly holds `korean-original`, `vietnamese`, `translation` material. |
| `title`, `authors` | **NEVER** | §11.2. |
| record deletion | **NEVER** | Positive proof of redundancy required. |
| OCR repair | **out of scope** | Belongs on ARC via omni-rag (`baidu/Unlimited-OCR`), not local `ocrmypdf`. `calinv` emits a queue file. |

The auto-fixable set is two fields, both currently with zero violations. That is the honest
answer to "should this tool have `--fix`": almost nothing, so the plan/apply split costs
little and buys the review step the spec demands.

---

## 12. Intentional exceptions: a `#dupok` custom column

**Reversing v1's recommendation**, because the evidence changed: the markdown allowlist was
already **4/7 dead** — entries guarding reports that never occur. A config file's silent
drift is not a hypothetical risk here; it already happened.

**Mechanism.** A Calibre custom column, created once:

```bash
calibredb add_custom_column dupok "Duplicate OK" text --is-multiple
```

`#dupok` holds **the ids this record is deliberately paired with**. So 1142 carries `1143`
and 1143 carries `1142`; the three *Artists' Pigments* volumes each carry the other two.

This answers v1's objection that "pairs are relations and a per-book field can't express
them": storing the partner ids makes the relation explicit and **symmetric**, and symmetry
is a checkable invariant. E1 suppresses a group **iff every member lists all the others**.
If one side is edited away, the pairing becomes asymmetric and **the group re-reports** —
the exemption fails open, never closed.

Why this beats a markdown allowlist:
- **It travels with the record** and is visible and editable in the GUI, where Adam is when
  he decides two copies are intentional.
- **It cannot be 4/7 dead**, because an exemption on a deleted record disappears with it,
  and `X1` lint reports `#dupok` values pointing at nonexistent ids and asymmetric pairings.
- Only one custom column exists today (`zotero`), so this is a small, precedented change.

Why not a tag: the library already contains a tag `duplicate-copy` on exactly 1 book — the
anti-pattern half-committed. A tag meaning "checker, ignore me" is indistinguishable from a
subject tag, pollutes `list_tags`, and shows up in every tag listing Adam reads.

**The prose reason goes in `comments`, not in the column** — the Schön pair's "index page
numbers drift +9 → +48, so 1143 is the citation-safe copy" explanation is for a human
reading the record. Machine-readable pairing in `#dupok`; human-readable justification in
`comments`; nothing in markdown.

Residual staleness: if a record keeps its id but is replaced with a different book, the
exemption persists. Mitigation is the same as everywhere else — suppressed findings are
**emitted, not filtered** (§3.2), so a suppression is always visible in the report and
countable, never silent.

---

## 13. What must not be automated

- **B1 title junk.** ~3 real of 86 flagged, zero hits for every genuine junk pattern
  (`PDF`, `z-lib`, `libgen`, `retail`), and it contradicts E2 (§0.5). Report-only, and
  arguably not worth reporting at all.
- **B2 author inversion.** ~2 real of 109, with two independent false-positive mechanisms:
  middle initials matching `\ba\b`, and the title-side heuristic hitting the naming
  convention of most maths/CS monographs. Corporate authors (`Editors of Vogue Patterns`)
  match forever. Report-only, with confidence, and never in a plan (§11.2).
- **C4 synonym clustering.** One pair library-wide, cannot pair its own four cited
  examples, and lower thresholds give `comics`~`economics`. Recommend **dropping the check**
  and keeping the four known clusters as prose in the skill, since the detector adds nothing
  the prose does not already say.
- **E1 resolution.** Report-only forever. Even the fixed normaliser cannot distinguish
  edition pairs, textbook/manual pairs, and volume sets from real duplicates — hence §12.
- **Resolution adequacy (D4).** Purpose-relative: 96 ppi fails for dimension tables and is
  fine for a photographic plate. Threshold flags; human decides.
- **OCR trustworthiness (D5).** Unautomatable by construction — requires knowing which
  pages hold the tables that matter. At best the tool verifies "digits extract from a page
  a human named."
- **Correct publication date (B4).** Implausibility is detectable (200 sentinels, 30
  post-2025 dates). The right date needs the CIP block: hydration plus reading.
- **`languages` (B5).** 738 records; the value is not in the DB (§11.3).
- **Tagless records (C1).** 33; requires knowing what the book is about.
- **A4 repair.** The only real defect, and its fix still moves a directory (§7).
- **Tag adoption.** No automatic vocabulary writes at all now (§10) — novelty is a prompt
  to a human at add time.

---

## 14. CLI surface

```
calinv preflight FILE --title T --authors A [--tags t1,t2] [--json] [--no-prompt]
                                                     # §5; non-zero blocks the add

calinv check    [--library PATH]
                [--check a4-case,e1,singleton-tags | --exclude …]
                [--content]                          # opt in to file reads
                [--fail-on {none,warn,fail}]         default: fail
                [--require-coverage content=0.9]
                [--format {text,json,jsonl,md}] [--quiet]
                [--hydrate-budget 0] [--hydrate-max-files 0] [--estimate]
                [--priority-tags anthropometry,pigments,optics,…] [--book 1142,1143]
                [--cache PATH] [--no-cache]
                [--emit-unexamined FILE] [--emit-plan FILE] [--emit-ocr-queue FILE]

calinv list     [--format {text,json}]               # registry: id, severity, layers, cost
calinv coverage                                      # what the cache knows
calinv plan     PLAN.json --explain
calinv apply    PLAN.json [--dry-run|--commit] [--only-safe]
calinv tags     singletons | novel | dump [--format md]
calinv dupok    lint | set ID --pairs 1142,1143      # via calibredb; §12
```

Defaults make the bare invocation the safe one: `calinv check` reads no file bytes, writes
nothing, and can legitimately exit 0.

---

## 15. Phasing

**Phase 0 — the core package.** `Library` (snapshot, read-only), `model`, `normalize` (the
one normaliser, with the CJK regression test and the subtitle/ordinal tests), `report`
(the `check_index.py`-aligned envelope), `registry`, `files` (tree walk with the
`.caltrash` exclusion, stat classifier), `fixtures/schema.sql`, `testing.py`. **Ships no
checks.** First because everything depends on it, because the `.caltrash` exclusion is a
correctness bug otherwise written twice, and because the CJK normaliser bug is live in
`omni-rag` today.

**Phase 1 — the preflight (§5).** Highest value per unit effort in the whole design: it is
free, it needs only Layer FS_CONTENT on a local staged file, and it prevents the entire
class of defect the rest of the tool struggles to detect after the fact. Also delivers the
tag-novelty prompt, which is the whole of the vocabulary replacement.

**Phase 2 — the free DB/stat checks: A4 case audit (§7), E1 fixed (§8), singleton tags
(§9), plus A1/A2/A3/A5a/C2/C3/C1 as tripwires.** One second of runtime, no file reads,
finds both of the library's real defects, and gives `calibre-mcp` something to expose
immediately. Consumer migration lands here: `omni-rag`'s
`calibre_metadata_audit.py` deletes its six local invariants and its duplicate grouping and
imports core; its ISBN checksum validation **moves into core**.

**Phase 3 — the measurement cache (§6)** plus content checks over the **resident set only**
(738 files, 66%, free). Delivers the coverage machinery under real conditions with a zero
hydration budget. **Prerequisite: settle Open Question 6 (does eviction rewrite `mtime`)
before building the cache key.**

**Phase 4 — the budgeted hydration path.** Priority ordering, `--emit-unexamined`, the
>50 MiB exclusion. Last, because it is the only part that spends real bytes and the cache
makes it a one-time cost worth paying deliberately.

**Phase 5 — plan/apply (§11)**, if wanted at all. Deliberately last: the safe-to-write set
is two fields with zero current violations, so its value is low and it can wait until the
report side has proven itself.

**Not building:** C4 (§13), a tag vocabulary schema (§10), OCR invocation, any delete.

---

## 16. Open questions

1. **Package name and repo home.** `calibre_core` is a placeholder. Does it live as a new
   top-level package in `MCPSuite/`, as a subpackage of `calibre-mcp` that omni-rag depends
   on, or as its own repo? This decides whether `omni-rag` gains a dependency on
   `calibre-mcp` — which would violate §1.2's spirit even if the import direction is legal.
   Recommend a standalone package.
2. **Does `calfuzz`'s scorer belong in core?** Keeping core stdlib-only means `difflib`
   rather than RapidFuzz. If the current blend depends on RapidFuzz semantics, either core
   takes its first third-party dependency or the scorer stays in `calibre-mcp` and only
   `fold` is shared. I did not inspect `calibre-mcp` (instructed not to), so this is
   unresolved.
3. **Cache location.** Sidecar in the library dir travels with the data and could be read
   by an ARC-side job, but it sits in OneDrive where a SQLite file risks sync-conflict
   copies. `~/.cache/calinv/` avoids that but is machine-local. Recommend `~/.cache` with a
   `--cache` override, and treat the cache as disposable/rebuildable either way.
4. **Should B1 and B2 be reported at all?** At ~3/86 and ~2/109 precision, the reports may
   cost more attention than they return. Options: drop them, or emit them only under an
   explicit `--heuristics` flag so they never appear in a routine run.
5. **B3's grey-literature exemption.** The spec says government reports and free monographs
   legitimately have no identifier and should be "exempt" without saying how. 231 records
   lack identifiers; the answer decides whether that is a 231-item list or a 40-item one.
   Candidate: reuse the custom-column pattern from §12.
6. **⚠ Does OneDrive eviction rewrite `mtime`?** Untested, and the highest-stakes unknown:
   if it does, the `(path, size, mtime)` cache key invalidates spuriously and Component 2
   silently stops working. Test: hydrate, record `mtime`, force eviction, re-`stat`.
   Blocks Phase 3.
7. **Per-format eligibility for content checks is undefined.** EPUBs have no ppi, so a
   resolution check must declare them *ineligible* rather than silently passing them — a
   passed-because-not-applicable is a coverage lie. Need a format × check matrix covering
   `.pdf`, `.epub`, `.djvu`, `.mobi`, `.azw3`, `.cbz`, `.txt`; the spec gives none.
8. **`PARTIAL` file state was never observed** (§0.3 is perfectly bimodal). The classifier
   keeps the bucket defensively, but it may be dead code on this Mac.
9. **Is the 954/995 pair actually byte-identical?** Same size, author, and work strongly
   suggest it, but confirming means hashing two 47 MB files. Worth doing once, by hand,
   before Adam decides which to remove — and it is the only place in this design where
   hashing earns its cost.
10. **Concurrency.** `snapshot=True` (§1.3) assumes a snapshot is wanted by default. If
    omni-rag ingest or the Calibre GUI never write during a run, it is 1.8 MB of needless
    copying — cheap, but the assumption should be confirmed.
