"""Shared read-only access to a Calibre library.

Exists because the same logic had been reimplemented three times across two
repos, with three different title normalisers -- one of which stripped CJK
characters and so silently removed every CJK-titled book from duplicate
detection. This is the single source for:

  * one read-only connection chokepoint (never a bare sqlite3.connect)
  * one canonical record model
  * one normaliser pair: `norm` for search reach, `dedup_key` for grouping

Writes are NOT here and will not be added. They go through calibredb /
calibre-debug new_api in calling code, because Calibre maintains derived state
(path layout, search caches, link tables) that direct SQL desynchronises.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
