"""Subprocess adapter for the `calibredb` binary and related filesystem ops."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from calibre_core.domain.write_rules import WriteBlocked
from calibre_core.infrastructure.sqlite import (
    configured_library,
    connect,
    db_path,
    library_path,
    override_db_path,
)

# Where the content server's own settings live. Its `port` line is the truth about
# where the server listens: Calibre falls back to it when 8080 is taken (here by
# Obsidian), and a probe of guessed ports then misses a running server and blocks
# every write with "no content server reachable".
SERVER_CONFIG_FILE = Path.home() / "Library" / "Preferences" / "calibre" / "server-config.txt"
_GUESSED_PORTS = (8080, 8181)

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
        subprocess.run(["pgrep", "-x", "calibre"], capture_output=True, check=False).returncode == 0
    )


def _configured_port() -> int | None:
    """The `port` from server-config.txt, read at CALL time (tests patch the path)."""
    if not SERVER_CONFIG_FILE.exists():
        return None
    for line in SERVER_CONFIG_FILE.read_text().splitlines():
        key, _, val = line.strip().partition(" ")
        if key == "port" and val.strip().isdigit():
            return int(val.strip())
    return None


def _candidate_ports() -> list[int]:
    """The configured port first, then the guesses, without repeats."""
    configured = _configured_port()
    ports = [configured] if configured else []
    return ports + [p for p in _GUESSED_PORTS if p != configured]


def _content_server_url() -> str | None:
    """Probe for a running Calibre content server with local-write enabled."""
    import urllib.error
    import urllib.request

    for port in _candidate_ports():
        url = f"http://localhost:{port}"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    r = subprocess.run(
                        [calibredb_path(), "list", "--limit", "1", "--with-library", f"{url}/#-"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
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
    # A write aimed at any library but the configured one lands in the live database whenever
    # Calibre redirects it: the content server writes to its own library, and an override
    # (macos-env.txt, read by every binary in calibre.app) wins over --with-library.
    if library_path().resolve() != configured_library().resolve():
        redirect = (
            "the GUI is open and the content server writes to its own library"
            if gui_is_open()
            else f"calibredb would write {override_db_path()} (database override)"
            if override_db_path()
            else None
        )
        if redirect:
            raise WriteBlocked(
                f"the target is not the configured library and {redirect} — refusing",
                {"target": str(library_path()), "configured": str(configured_library())},
            )
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
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise WriteBlocked(f"calibredb failed: {(r.stderr or '').strip()[:400]}")
    return (r.stdout or "").strip()


def current_identifiers(book_id: int) -> dict[str, str]:
    """The record's identifiers as {type: value}. Read-only."""
    con = connect()
    try:
        return dict(con.execute("SELECT type, val FROM identifiers WHERE book=?", (book_id,)))
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
