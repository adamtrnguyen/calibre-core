"""Subprocess adapter for the `calibredb` binary and related filesystem ops."""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from calibre_core.domain.write_rules import WriteBlocked
from calibre_core.infrastructure.sqlite import connect, db_path, library_path


_CALIBREDB_FALLBACKS = (
    "/opt/homebrew/bin/calibredb",
    "/usr/local/bin/calibredb",
    "/Applications/calibre.app/Contents/MacOS/calibredb",
)


def calibredb_path() -> str:
    """Locate the `calibredb` binary, PATH first."""
    found = shutil.which("calibredb")
    if found:
        return found
    for cand in _CALIBREDB_FALLBACKS:
        if os.path.exists(cand):
            return cand
    raise WriteBlocked(
        "calibredb not found on PATH or in the known install locations",
        {"searched": ["$PATH", *_CALIBREDB_FALLBACKS]},
    )


def gui_is_open() -> bool:
    """True if the Calibre GUI is running."""
    return (
        subprocess.run(["pgrep", "-x", "calibre"], capture_output=True, check=False).returncode
        == 0
    )


def _content_server_url() -> str | None:
    """Probe for a running Calibre content server with local-write enabled."""
    import urllib.error
    import urllib.request
    for port in (8080, 8181):
        url = f"http://localhost:{port}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    r = subprocess.run(
                        [calibredb_path(), "list", "--limit", "1",
                         "--with-library", f"{url}/#-"],
                        capture_output=True, text=True, check=False, timeout=10,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        lib = r.stdout.strip().splitlines()[0].strip()
                        return f"{url}/#{lib}"
        except (urllib.error.URLError, OSError, subprocess.TimeoutExpired):
            continue
    return None


def backup_db(dest_dir: str = "/tmp/calibre-db-backups") -> str:
    """Copy metadata.db aside, returning the backup path."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"metadata.db.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(str(db_path()), dest)
    return dest


def run_calibredb(args: list[str]) -> str:
    """Execute a calibredb command, routing through the content server if needed."""
    lib_target = str(library_path())
    if gui_is_open():
        server_url = _content_server_url()
        if server_url:
            lib_target = server_url
        else:
            raise WriteBlocked(
                "Calibre GUI is open and no content server with local-write is "
                "reachable. Either close the GUI, or enable the content server: "
                "Preferences → Sharing over the net → Advanced → enable local write, "
                "then Connect/share → Start Content server."
            )
    r = subprocess.run(
        [calibredb_path(), *args, "--with-library", lib_target],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise WriteBlocked(f"calibredb failed: {(r.stderr or '').strip()[:400]}")
    return (r.stdout or "").strip()


def current_identifiers(book_id: int) -> dict[str, str]:
    """The record's identifiers as {type: value}. Read-only."""
    con = connect()
    try:
        return {
            t: v for t, v in con.execute(
                "SELECT type, val FROM identifiers WHERE book=?", (book_id,)
            )
        }
    finally:
        con.close()


def merge_identifiers(book_id: int, incoming: str) -> str:
    """Fold `incoming` over the record's existing identifiers.

    `calibredb set_metadata --field identifiers:...` REPLACES the whole set, it
    does not merge. This merges: same type overwrites, everything else carries.
    """
    merged = current_identifiers(book_id)
    for pair in (incoming or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        t, _, v = pair.partition(":")
        merged[t.strip()] = v.strip()
    return ",".join(f"{t}:{v}" for t, v in sorted(merged.items()))
