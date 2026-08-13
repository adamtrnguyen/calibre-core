# calibre-core

The one place that knows what a [Calibre](https://calibre-ebook.com/) library is.

Reads a library's `metadata.db`, normalises titles and authors, finds duplicates
and integrity faults, and writes through a gate. Everything else — an MCP server,
a CLI, an Obsidian plugin, a RAG ingest pipeline — is an interface over this.

## Why it exists

The same logic had been reimplemented across 15 files in 4 repos, with **three
divergent title normalisers**. The differences were silent and expensive:

- One stripped CJK characters, so every Chinese, Japanese and Korean title
  normalised to the empty string. The callers filtered empty keys, so those books
  were not mis-grouped — they were **absent** from duplicate detection, with no
  error anywhere.
- Search and duplicate-grouping disagreed about accents, because one folded
  diacritics and the other did not.
- A duplicate check keyed on title alone, so *Algebra* by Lang and *Algebra* by
  Artin were the same book.

There was no single answer to "what is a Calibre record, and is it valid". This is
that answer.

## Install

```toml
[project]
dependencies = ["calibre-core"]

[tool.uv.sources]
calibre-core = { git = "https://github.com/adamtrnguyen/calibre-core", tag = "v0.4.0" }
```

`pymupdf` is an optional extra (`calibre-core[pdf]`), needed only by the two
things that read or write PDF internals: the audit's ISBN scan and the outline
write. Everything else runs on the base install.

## Use

```python
from calibre_core import load_books, dedup_key, title_groups, orphan_dirs

for b in load_books():
    print(b.id, b.title, b.calibre_url)

title_groups()      # duplicate candidates: normalised title + first-author surname
orphan_dirs()       # directories on disk with no row in metadata.db
```

```python
from calibre_core import inject_outline

# Write a table of contents into a library PDF: refuses while the Calibre GUI is
# open, saves elsewhere and verifies the copy before it replaces the original,
# keeps a backup. Returns {"ok": False, "reason": ...} per file; raises
# WriteBlocked only for the GUI, which applies to the whole batch.
inject_outline(path, entries, backup_dir="/tmp/toc-backups")
```

The CLI serves two populations: callers that cannot import Python (an Obsidian
plugin spawned with a bare interpreter, a shell script), and tools you run.

```console
$ calibre-core books --json
$ calibre-core resolve "/path/to/Author/Title (123)/book.pdf" --json

# metadata cleanup, in two composable halves — the audit is the expensive one,
# so it writes a JSON the second step reads as often as you like
$ calibre-core audit --out-dir reports --scan-missing-isbns
$ calibre-core metadata-candidates reports/calibre-metadata-audit-<stamp>.json
```

`audit` finds records with no valid ISBN, no publisher, no pubdate, a placeholder
author or a filename where a title should be, and reports duplicate groups.
`metadata-candidates` looks each ISBN up on Open Library and labels the match
`strong-candidate`, `review-title-author`, or `lookup-error` — it never writes to
Calibre. Both reports are timestamped, because comparing one to the last is the
point.

## The two invariants

**Reads are read-only by construction.** `connect()` opens with `?mode=ro` and
cannot write, so that is a property of this package rather than of whoever writes
the next call site. Before it existed, `?mode=ro` was copy-pasted at seven sites
and one of them was a read-write connection used for a `SELECT`.

**Writes never issue SQL.** They shell out to `calibredb`, because Calibre
maintains derived state — path layout, search caches, link tables — that direct
SQL desynchronises. The write path also enforces what is easy to forget: the GUI
must be closed, `metadata.db` is backed up first, duplicates are detected before
the record exists, and `title`/`authors` are mandatory on add (without them
`calibredb add` parses the filename as `Title - Author`, while most sources name
files `Author - Title`, so the record lands silently inverted).

## Notes for anyone else

Two behaviours look like bugs and are not:

- `library_path()` returns the path **unresolved**. A library is often a symlink
  into cloud storage, and resolving it changes `Path.relative_to` output in orphan
  scanning. Call `.resolve()` yourself where you need the physical path.
- Duplicate detection reads sizes from the `data.uncompressed_size` **column**,
  never by stat-ing files, and hashes only a size collision. On a cloud-synced
  library, reading one byte of a dataless placeholder downloads the whole file, so
  a hashing sweep is a multi-gigabyte transfer.

`#dupok` is a Calibre custom column naming ids a record is allowed to duplicate.
Suppression is pairwise: two records must name **each other**, because a flattened
allowlist means a book named by anyone is excused against everyone.

## Tests

287 tests, and each one names the defect it prevents. The normaliser tests are
regression locks against real failures, not synthetic cases — `test_normalize.py`
exists because a test asserting only Chinese passed for months while Korean was
silently broken.

```console
$ just qa          # the whole gate: lint + anti-sprawl + tests
$ just test        # pytest alone
$ just lint        # ruff, ty (types), lint-imports (no consumer may be imported)
$ just sprawl      # vulture (dead code) + codespell
$ just audit       # uv lock --check + pip-audit CVE scan; separate, not in `qa`
```

`ty` is pinned to an exact version in the dev group: it is pre-1.0, so an
unpinned bump can turn the gate red with no change to this code.
