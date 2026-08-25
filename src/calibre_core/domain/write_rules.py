"""Domain rules for the write gate — no I/O, no subprocess."""

from __future__ import annotations


class WriteBlocked(Exception):
    """A gate refused the operation. Carries a machine-readable reason."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


_HTML_ENTITIES = ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;", "&apos;", "&nbsp;")


def reject_html_entities(**fields: object) -> None:
    """Refuse HTML-escaped text in metadata headed for calibredb.

    `&` is Calibre's AUTHOR SEPARATOR, so "A &amp; B" is not merely ugly --
    calibredb splits it into "A" plus a phantom author "amp; B". Observed for
    real: authors="Rudolf von Laban &amp; Lisa Ullmann" stored
    ['Rudolf von Laban', 'amp; Lisa Ullmann'] and reported success.

    Enforced because the field it damages worst is the one with NO repair path:
    `set_book_metadata` refuses `authors` unconditionally, so the only fixes are
    remove-and-re-add or the GUI. A gate that permits an unfixable write is not
    a gate -- the same reasoning that put the GUI check and the duplicate check
    in here rather than in a caller's docstring.

    Entities arrive from scraped listings, OPF files and XML sidecars, i.e. the
    ordinary sources for this metadata, so this is the common case.

    Non-str values pass through untouched; a `fields` dict legitimately carries
    ints and None.
    """
    for name, value in fields.items():
        if not isinstance(value, str):
            continue
        for ent in _HTML_ENTITIES:
            if ent in value:
                raise WriteBlocked(
                    f"{name!r} contains the HTML entity {ent!r} — pass literal "
                    f"characters (a real '&', not '&amp;'). In authors '&' is the "
                    f"separator, so this would silently create a phantom author.",
                    {"field": name, "value": value, "entity": ent},
                )
