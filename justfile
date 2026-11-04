[private]
default:
    @just --list

# The gate: every check at once, every failure named. Each PID is waited on by itself (a bare
# `wait` returns only the last job's status); `failed` is a string because macOS bash 3.2 errors
# on an empty array under `set -u`. The gate never rewrites files: `just fix` is the fixer.
# Why it is shaped this way: docs/history/concurrent-qa-gate.md.
[doc("the gate: every check at once, every failure named")]
qa:
    #!/usr/bin/env bash
    set -uo pipefail
    names=(lint fmt-check types layers dead spell test)
    logs=$(mktemp -d "${TMPDIR:-/tmp}/qa.XXXXXX")
    trap 'rm -rf "$logs"' EXIT
    echo "== qa: ${#names[@]} checks, concurrent (${names[*]}); output replayed in that order =="
    pids=()
    for name in "${names[@]}"; do
        just "$name" >"$logs/$name.out" 2>&1 &
        pids+=("$!")
    done
    failed=""
    for i in "${!pids[@]}"; do
        wait "${pids[$i]}" || failed="$failed ${names[$i]}"
    done
    for name in "${names[@]}"; do cat "$logs/$name.out"; echo ""; done
    if [ -n "$failed" ]; then
        echo "QA FAILED:$failed"
        exit 1
    fi
    echo "QA passed: ${names[*]}."

[doc("ruff check, no fixes")]
lint:
    @echo "== ruff check =="
    uv run ruff check .

[doc("ruff format --check: the tree stays formatted (fix with `just fix`)")]
fmt-check:
    @echo "== ruff format --check =="
    uv run ruff format --check .

[doc("ty type check of src/ (pinned in the dev group)")]
types:
    @echo "== ty =="
    uv run ty check src/

[doc("import-linter contracts (.importlinter)")]
layers:
    @echo "== import-linter =="
    uv run lint-imports

[doc("vulture dead-code scan")]
dead:
    @echo "== vulture =="
    uv run vulture src/ scripts/ --min-confidence 80

# tests/ is included: the test names and docstrings carry the reasoning, so a typo there is a
# typo in the record.
[doc("codespell over source, tests and README")]
spell:
    @echo "== codespell =="
    uv run codespell src/ scripts/ tests/ README.md

[doc("pytest, the whole suite")]
test:
    @echo "== pytest =="
    uv run pytest

# Fixers: opt-in, never part of qa.
[doc("rewrite in place: ruff check --fix, then ruff format")]
fix:
    uv run ruff check --fix .
    uv run ruff format .

# The `pdf` extra path: `toc`'s outline write and `audit`'s ISBN scan import
# pymupdf inside the function, so a base-install run silently skips them. pymupdf
# is in the dev group, so plain `just test` already covers both -- this proves the
# EXTRA resolves, which is what a consumer installs.
test-pdf:
    uv run --extra pdf pytest

# Lockfile consistency only. CVE scanning was removed 2026-08-13: this is personal
# tooling over PDFs Adam sources himself, not a service taking untrusted input, so the
# threat model does not justify the noise. `uv lock --check` stays — it catches a
# pyproject/uv.lock drift, which IS a real and frequent failure here (bumping the
# calibre-core tag without re-locking).
#
# For the record, the last honest run found: torch 2.11.0 PYSEC-2025-194 (fix 2.13.0)
# and setuptools 81.0.0 PYSEC-2026-3447 (fix 83.0.0). Neither acted on. If this is ever
# reinstated, note that pip-audit needs --no-deps --disable-pip (its throwaway resolver
# venv SIGABRTs in ensurepip on this machine) and that git-URL requirements must be
# stripped, or it exits 1 on "URL requirements cannot be pinned".
audit:
    uv lock --check

# pre-commit is not a dev dependency anywhere in the suite, so it is run through
# uvx rather than `uv run` (which fails with "Failed to spawn: pre-commit").
setup-hooks:
    uvx pre-commit install

clean:
    rm -rf .pytest_cache .ruff_cache .ty_cache .import_linter_cache
    find . -type d -name "__pycache__" -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete
