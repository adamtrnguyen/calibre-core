"""The canonical Book record — a frozen value object with no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    formats: tuple[Path, ...] = ()
    sizes: tuple[int, ...] = ()
    isbn: str | None = None
    path: str = ""
    uuid: str | None = None
    timestamp: str | None = None
    pubdate: str | None = None
    last_modified: str | None = None
    publisher: str | None = None
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def authors_str(self) -> str:
        """Ampersand-joined, matching the house convention and the old SQL."""
        return " & ".join(self.authors)

    @property
    def calibre_url(self) -> str | None:
        """A clickable deep link. Needs the uuid — the numeric id will not do."""
        return f"calibre://show-book/_hex_-43616c69627265/{self.uuid}" if self.uuid else None


def split_field(s: str | None, sep: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in (s or "").split(sep) if x.strip())
