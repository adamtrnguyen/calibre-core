# 2026-11-04: the concurrent `qa` gate, pinned tools, installed hooks

## What changed

- `just qa` was `qa: lint sprawl test`, a chain of `just` dependencies. It is now the fleet's
  concurrent gate: lint, fmt-check, types, layers, dead, spell and test run at once, each PID is
  waited on separately, every log is replayed in order, and the gate ends with
  `QA FAILED: <names>` or `QA passed`.
- `fmt-check` (`ruff format --check`) joined the gate. The tree was formatted once, in a
  format-only commit. The old `format` recipe is now `fix`, an opt-in fixer outside the gate.
- `ruff==0.17.2` and `ty==0.0.88` are pinned exactly (were `ruff>=0.6` and `ty==0.0.55`). The
  pre-commit ruff hook `rev` is `v0.17.2`, the same version. The ty bump raised no new
  diagnostics.
- Pre-commit: the fixers (pre-commit-hooks, ruff, ruff-format) and gitleaks run on commit.
  `uv lock --check` still runs when pyproject.toml or uv.lock changes. The hooks that restated
  gate checks (codespell, vulture, lint-imports) were replaced by one pre-push hook,
  `entry: just qa`. The hooks are installed (`uvx pre-commit install`).
- Ruff `select` gained the fleet base rules `W`, `C4` and `TID252`.

## Why

The 2026-11-04 audits of Adam's repos found the same three failures everywhere:

1. **The sequential `qa` hid failures.** just stops at the first failing recipe. Here, ruff had
   been red for 71 days (since 2026-08-25, across tags v0.8.0, v0.9.0 and v0.9.1) with 14
   errors: 11 I001, 2 F401 in `application/writes.py` and 1 E501. So ty, lint-imports,
   vulture, codespell and pytest never ran inside `qa`. What that hid was a test that
   reached the live library (below).
2. **The hooks were never installed.** `.pre-commit-config.yaml` existed, but the hooks
   directory held only `*.sample` files. Its ruff `--fix` hook would have repaired every I001
   at commit time.
3. **Versions floated.** The gate ran whatever ruff `>=0.6` resolved to (0.16.2), and the hook
   ran v0.15.0. The two sort imports and format differently. The config also had a
   `ruff-format` hook while the justfile called formatting "never a gate", a contradiction
   that would have reformatted the first commit touching any of the 27 unformatted files.

## The live-library write (why the tests stub the content server)

v0.9.1 (fb4f670) made the content-server probe read Calibre's configured port (8081). Before
that it guessed 8080 (held by Obsidian) and 8181, found nothing, and refused the write.
`test_a_title_write_still_refuses_while_the_gui_is_open` fakes "GUI open" but did not stub the
probe. With Calibre running, the probe found the live server and the test sent
`set_metadata 1 --field title:Better Title` to the LIVE library. The write failed only because
the live library has no book id 1. 5565946 fixed both sides:

- `run_calibredb` refuses the content-server route unless the target is the configured library.
- The autouse `_gui_closed` fixture stubs `_content_server_url` for every test.

Before the new gate first ran, the suite was re-run under an external guard plugin. It blocked
and recorded any `calibre*` exec, any TCP connect, and any sqlite open of the live database or
library. It recorded 0 attempts across 316 tests. Note that `macos-env.txt` sets
`CALIBRE_OVERRIDE_DATABASE_PATH` to the live database, and Calibre's own binaries read it. A
real `calibredb` run from a test would therefore reach the live database whatever
`--with-library` names. Every write test must mock `run_calibredb` or be refused before it.
