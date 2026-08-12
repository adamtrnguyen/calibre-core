"""Step-1 proof: the package installs, imports, and cannot reach its consumers."""

import importlib

import pytest


def test_package_imports():
    import calibre_core

    assert calibre_core.__version__ == "0.1.0"


@pytest.mark.parametrize("forbidden", ["mcp", "fastmcp", "omnirag", "lancedb"])
def test_consumer_packages_are_not_installed(forbidden):
    """The dependency direction is enforced by absence, not convention.

    If this starts passing-by-import, someone added a dependency that pulled a
    consumer into the core's environment.
    """
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(forbidden)
