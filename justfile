[private]
default:
    @just --list

# static checks: style, types, layering
lint:
    uv run ruff check .
    uv run ty check src/
    uv run lint-imports

# auto-fix style. NOT part of `qa`: this repo is not ruff-format-clean yet
# (25 files would change), so formatting is an explicit choice, never a gate.
format:
    uv run ruff check --fix .
    uv run ruff format .

# anti-sprawl: dead code + typos. tests/ is included because the test names and
# docstrings here carry the reasoning — a typo in a name is a typo in the record.
sprawl:
    uv run vulture src/ scripts/ --min-confidence 80
    uv run codespell src/ scripts/ tests/ README.md

# full gate (mirrors omni-rag): lint + anti-sprawl + tests
qa: lint sprawl test

test:
    uv run pytest

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
