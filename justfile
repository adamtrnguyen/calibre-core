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

# supply-chain: lockfile consistent with pyproject + CVE scan of the locked
# runtime deps. Separate from `qa` so an upstream CVE can't block the gate.
#
# Two deviations from omni-rag's version of this recipe, both deliberate:
#   `trap … EXIT` instead of a trailing `; rm -f` -- the trailing form makes `rm`
#     the last command, so the recipe exits 0 even when pip-audit reports a CVE.
#   `--no-deps --disable-pip` -- `uv export` already emits the FULL transitive
#     closure pinned to exact versions, so pip's resolver has nothing to add. It
#     also has to go: pip-audit resolves in a throwaway venv whose `ensurepip`
#     dies with SIGABRT on this machine, which is what made the scan unrunnable.
audit:
    uv lock --check
    req=$(mktemp); trap 'rm -f "$req"' EXIT; uv export --frozen --no-emit-project --no-dev --no-hashes --format requirements-txt 2>/dev/null | grep -viE '^-e |file://' > "$req"; uvx pip-audit -r "$req" --no-deps --disable-pip

# pre-commit is not a dev dependency anywhere in the suite, so it is run through
# uvx rather than `uv run` (which fails with "Failed to spawn: pre-commit").
setup-hooks:
    uvx pre-commit install

clean:
    rm -rf .pytest_cache .ruff_cache .ty_cache .import_linter_cache
    find . -type d -name "__pycache__" -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete
