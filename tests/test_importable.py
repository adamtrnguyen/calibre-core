"""Step-1 proof: the package installs, imports, and cannot reach its consumers."""

import importlib
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_package_imports():
    import calibre_core

    assert calibre_core.__version__


def test_version_matches_pyproject():
    """The version is written in TWO places and nothing kept them in step.

    Consumers pin on the pyproject version (`calibre-core>=0.2.0`) but read
    `__version__` at runtime, so a bump applied to one and not the other is
    invisible until someone debugs why a pin that resolved does not have the
    feature. Read from pyproject rather than installed metadata deliberately: dist
    metadata goes stale until the editable install is refreshed, which would make
    this pass while the file on disk disagrees.
    """
    import calibre_core

    declared = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    assert calibre_core.__version__ == declared


def test_everything_in_all_is_actually_importable():
    """`__all__` is the package's advertised API and it is hand-maintained in a
    deliberately non-alphabetical grouped list, so a name can be added to it
    without an import (or misspelled) and nothing notices. A consumer then reads
    the list, writes `from calibre_core import <name>`, and gets ImportError —
    which is how a shared API loses a caller to a local reimplementation."""
    import calibre_core

    missing = [n for n in calibre_core.__all__ if not hasattr(calibre_core, n)]
    assert missing == []


def test_the_pairwise_dupok_api_is_reachable_from_the_package_root():
    """Gap this closes: `_excused` was underscore-private for a release, so
    calibre-mcp wrote `_excused_within` and omni-rag's audit wrote `_all_excused`
    rather than import it. Both reimplementations got the semantics wrong in the
    same direction. A consumer must be able to reach these without touching a
    submodule."""
    import calibre_core
    from calibre_core import excused, excused_within

    assert excused(1, 2, {1: {2}})
    assert excused_within(1, [1, 2], {1: {2}})
    assert {"excused", "excused_within"} <= set(calibre_core.__all__)


@pytest.mark.parametrize("forbidden", ["mcp", "fastmcp", "omnirag", "lancedb"])
def test_consumer_packages_are_not_installed(forbidden):
    """The dependency direction is enforced by absence, not convention.

    If this starts passing-by-import, someone added a dependency that pulled a
    consumer into the core's environment.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(forbidden)
