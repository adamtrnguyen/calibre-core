# calibre-core — decisions and open items

> **ARCHIVED DESIGN RECORD — relocated 2026-08-12 from `~/Research/infra/calibre-check-wip/`
> (deleted; was never under version control).** This is the design rationale behind
> `calibre-core`, which has since shipped at **v0.2.0** (tag `calibre-core-v0.2.0`).
>
> **This is a historical record, not a live worklist.** Blockers 1 and 3 below were
> re-checked on relocation and are **RESOLVED** — see the strikethrough notes in
> *Blockers and open questions*. The remaining open questions were **not** re-verified,
> so treat every one of them as unconfirmed until checked against the shipped package.

Output of a four-agent design pass. Everything below was measured against the live
library or verified by running it, except where marked *inferred*.

## The problem

No single answer anywhere to "what is a Calibre record, and is it valid." Logic is
duplicated across **15 files in 4 repos**, with three divergent title normalisers:

| Where | Behaviour |
|---|---|
| `calibre-mcp/matching.py::norm` | NFKD + casefold, CJK-preserving — correct |
| `calibre-mcp/matching.py::duplicate_groups.key` | `.lower()`, no NFKD, CJK-preserving — diverges from `norm`, so search and dedup disagree on accents |
| `omni-rag/scripts/calibre_metadata_audit.py::normalized_title` | strips `[^a-z0-9]`, **no CJK** — every CJK title → `''`, and `classify()` filters `if key`, so **CJK books are silently absent from duplicate detection** |

Plus five copy-pasted `?mode=ro` connect sites in calibre-mcp, two more in
`omni-rag/.../calibre_uuid_resolver.py`, and a **bare read-write** `sqlite3.connect()`
in `omni-rag/scripts/reconcile_library.py:29` used for a SELECT.

## Decisions

1. **Home: `CalibreSuite/packages/calibre-core`** — a plain directory in the existing
   CalibreSuite meta-repo. Not a submodule (no pointer dance), not inside `calibre-mcp`
   (the name would lie, and omni-rag would inherit the `mcp[cli]<2` ceiling), not a path
   dependency (that is exactly why omni-rag cannot `uv sync` today).
   CalibreSuite's own history is the precedent: it vendored `omnirag-search` as a subtree,
   then dropped it — *"the two copies drifted within an hour. One source beats any sync
   discipline."*
2. **Consumed by git URL + subdirectory + tag.** Verified with uv 0.11.21: `uv lock`
   resolves and pins to a SHA. `uv add` has no `--subdirectory` flag — write
   `[tool.uv.sources]` by hand. For co-development, temporarily swap to
   `{ path = "...", editable = true }`; commit only the git form.
3. **Distribution `calibre-core`, import package `calibre_core`.** Never `calibre` —
   omni-rag's `calibre-plugin/` imports the real `calibre.*` inside Calibre's interpreter.
4. **Reads: direct sqlite via one private `_connect()`** doing
   `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`. **Writes: not in the core at all** —
   they stay in calling code via `calibredb`/`calibre-debug new_api`, because Calibre
   maintains derived state. Reads and writes are different operations, not two adapters
   behind one port.
5. **`requires-python = ">=3.11"`**, developed on 3.11 — calibre-mcp floors at 3.13 but
   omni-rag caps at `<3.14`. `matching.py` needs nothing past 3.10 syntax.
6. **Dependency direction enforced three ways**: `dependencies = []` (stdlib only, so
   `mcp` is not installable let alone importable); an `.importlinter` forbidden contract in
   the core; and a *second* contract in omni-rag, because its existing `layers` contract
   does not police external packages — only `omnirag.infrastructure` may import
   `calibre_core`, keeping it behind the existing `BookIdentifier` port.
7. **Two normalisers stay public and separate.** Measured: `'The Geometry of an Art, 2nd
   Edition'` → `norm` = `'the geometry of an art 2nd edition'` (search reach),
   `dedup_key` = `'geometry of art'` (grouping strictness). Collapsing them changes search
   results. `dedup_key` is a composition over `norm`; both CJK-preserving. This is the fix
   for all three divergent normalisers.
8. **Scope of the checker: ~6 checks, not 24.** Six invariants find *zero* violations
   (A1, A2, A3, A5, C2, C3); the measured defect rate is 1 / 1,094 = 0.09%. The library is
   already correct because the **add pipeline enforces conventions at write time** — so
   strengthen the write path, don't build an audit.
9. **Do not encode the tag vocabulary.** 363 tags live, ~145 documented, **211
   undocumented (58%)**, and zero documented tags are unused — drift is one-directional.
   Encoding it converts a stale document into a stale assertion repeated 211×/run, and the
   check gets disabled. Instead: **vocabulary = `SELECT name FROM tags`**, plus an add-time
   novelty prompt, plus a singleton report (165 tags used once — which is where the real
   junk lives, including a tag literally named `5` and one that is a whole sentence).
10. **Intentional duplicate pairs live in a Calibre custom column** (`#dupok`, holding
    partner ids so pairings are symmetric and fail open), not a markdown allowlist. The
    markdown list was already 4/7 dead — it named pairs that never group and missed all
    four series that do.
11. **No `--fix` for title or authors, ever.** A retitle moves a directory in a 32 GiB
    OneDrive-backed tree and `metadata.db` rollback reverts the catalogue but **not** the
    filesystem — there is no rollback, only forward repair. A title↔author swap is also
    non-idempotent, so fix-then-verify reports clean whether the fix worked *or* corrupted a
    correct record. Additive fixes (tags, languages) are safe.

## What to build, in value order

1. **Add-time preflight** on the staged local file before `calibredb add`: text-layer/ppi
   sample, duplicate check by normalised title+surname **and by exact byte size**,
   tag-novelty prompt. Every confirmed real defect would have been caught here for free.
2. **Class D result cache** — sidecar SQLite keyed by `(path, size, mtime)`. Each file
   hydrates once, ever. Without it every sweep re-pays full hydration and never converges.
3. **A4 case-exact path audit** — one second, no file reads, found the only structural
   defect. Run after every rollback and before every rsync to a case-sensitive target.
4. **E1 duplicates, report-only**, with the fixed normaliser.
5. **Singleton-tag report.**

## Measured facts worth keeping

- **`st_blocks == 0` is an exact placeholder classifier** — perfectly bimodal: 378 files at
  0.0, 738 at ≥0.95, nothing between.
- **Partial reads are impossible.** A 4,096-byte read of a 10.06 MiB dataless PDF hydrated
  all 10.07 MiB in 4.51 s. So the spec's A5 magic-byte test is a 10.83 GiB full-library
  download wearing the label of a cheap BLOCKER.
- **Eviction preserves `mtime`** — *inferred* from distribution, not observed across a
  single eviction: dataless mtimes spread over **53 distinct dates** with
  `|mtime − date_added|` median **0.0 days**, matching the hydrated set. Had eviction
  rewritten mtime they would bunch on recent dates. This is what makes the cache key valid.
- **`.caltrash` holds 99 files / 839 MiB** across 33 book dirs at depth 4. Any unexcluded
  tree walk reports deleted books as orphans *and* hydrates 839 MiB of discarded books. It
  also resolves the id-gap question (1,094 books vs max id 1,174).
- **`calibredb list` is not read-only.** Against a hand-built fixture it rewrote the file
  into a 48-table Calibre schema and dropped every row. **Never build fixtures with it.**
- **E1 measured both ways**: subtitle-stripped = 11 groups / 32 records (including all 9
  Morpho volumes); subtitle-kept = **4 groups / 8 records**. When adding spelled-out
  ordinals to catch `Fifth Edition`, strip the ordinal **only when immediately followed by
  `edition`** — otherwise `The First/Second Tutorial…` loses its number.
- **Two real defects found, both invisible to the original audit**: id 66 case-only path
  drift (fixed), and **ids 954 + 995**, Sidney F. Ray *Applied Photographic Optics*, both
  PDF, both exactly **49,737,184 bytes** — long-form vs short-form title, findable only by
  exact-size grouping.
- **B2's false positives have two independent causes**: the title-side heuristic flags
  *Convex Optimization*, *Graph Theory*, *Radiative Transfer*, *Fluent Python* (i.e. the
  naming convention of most maths/CS monographs), and `\ba\b` matches middle initials like
  *Beverly A. Sanders*.
- **A4 must import `calibre.utils.filenames.ascii_filename`** under `calibre-debug`. A
  hand-rolled NFKD version produced 10 violations of which 6 were CJK false positives
  (陶哲轩实分析 → `Tao Zhe Xuan Shi Fen Xi`); the real function gives
  **1,093 exact / 1 case-only / 0 drift**.

## Blockers and open questions

1. ~~**`~/Research/slurp` does not exist**, so omni-rag's `slurp-serve` path dep fails and
   `uv sync`/`just lint`/`just test` cannot run. Was it moved to ARC/NAS deliberately, or
   lost? Blocks every omni-rag migration step. **Adam's call.**~~
   **✅ RESOLVED — verified 2026-08-12 on relocation.** `/Users/adam/Research/slurp` is
   present (contains `justfile`, `configs/`, `docs/`, `extern/`, …) and
   `uv lock --check` in `~/Research/omni-rag` reports `Resolved 218 packages in 4ms`.
   The path dep resolves; nothing here blocks an omni-rag migration step.
2. **`Book.formats` semantics are ambiguous** — the audit needs absolute file paths,
   the MCP `get_book` returns format codes. Different shapes; needs a pass over every
   consumer before fixing the field's meaning.
3. ~~**The MCP chezmoi entry lacks `--no-sync`** (the calfuzz wrapper has it), so every
   Claude Code launch resolves dependencies. Adding a git dep therefore creates a
   startup-time failure mode when offline or with a cold cache. Consider adding `--no-sync`.~~
   **✅ RESOLVED — verified 2026-08-12 on relocation.** `--no-sync` is present in
   `.chezmoitemplates/claude-mcp-servers.json` for **both** the `calibre` entry (line 109)
   and the `omni-rag` entry (line 182). The startup-time failure mode is closed.
4. **OCR is a three-way contradiction** — skill now says ARC-only, the target-state spec
   still prescribes `ocrmypdf` flags, and logs show local tesseract ran on four books.
   Adam settled it (ARC with a model); the spec needs the same edit the skill already got.
5. **Whether `calibre2zotero` and `calibre-page-inserter` are live.** Only their
   Calibre-access lines were read, not their flow.
6. **Where the checker itself lives.** If it becomes a console script inside
   `calibre-core`, the core stops being dependency-free.

## Skill split (separate from the package work)

- `calibre` shrinks from 599 → **~150 lines**. Sorting axis: **detection → code; decision
  and mutation → prose.**
- New `calibre-acquire` skill owns everything up to "a verified file is sitting in
  `$STAGE`"; `calibre` owns everything from `calibredb add` onward, **including the `add`
  invocation and the title/author inversion trap**, so that trap sits on exactly one side
  of the seam.
- **`$STAGE` = the session scratchpad**, defined once. `/tmp` is where Anna's MCP *drops*
  files, so documenting it as the target makes "move it out of /tmp" vacuous. `domain-canon`
  has a fourth variant (`/tmp/calibre-staging/`) to delete.
- Reference rules that stop prose regrowing: never restate a threshold or regex; refer by
  invariant ID not command line; show exactly one invocation; name data files, not their
  contents; and state the contract — *if this disagrees with the tool, the tool wins, and
  fix this file.*
- Delete `/Users/adam/Calibre Library/CLAUDE.md` (stale: wrong count, Title-Case tags).
  Five lines of the skill exist only to warn about it.
