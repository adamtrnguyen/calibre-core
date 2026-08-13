"""Locks on the CLI — the only interface that ships inside this package.

It is here because calibre-page-inserter cannot import Python at all: Obsidian
spawns its helper with a bare `python3`, no venv, stdlib only. So these tests
guard the shape that subprocess parses, and the two places the shape differs from
the library's own (format CODES instead of paths, JSON instead of `Book`).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from calibre_core.cli import main
from calibre_core.records import load_books


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _json_out(capsys, argv: list[str]):
    code, out, err = _run(capsys, argv)
    assert code == 0, err
    return json.loads(out)


# --------------------------------------------------------------------------
# books -- what the plugin's picker is built from
# --------------------------------------------------------------------------

def test_books_carries_the_fields_the_plugin_consumes(library, capsys):
    library.add(1, "A Book", authors="A Writer")
    (row,) = _json_out(capsys, ["--json", "books"])
    assert row == {
        "id": 1,
        "uuid": "uuid-1",
        "title": "A Book",
        "authors": "A Writer",
        "formats": ["PDF"],
    }


def test_formats_are_codes_not_paths(library, capsys):
    """`Book.formats` carries absolute PATHS and stays that way — the reduction to
    codes happens here, at the edge, because that is what a caller building
    `calibre://…/<FMT>?open_at=` needs. Exact rather than a guess: Calibre names
    each file `<name>.<format.lower()>`."""
    pdf = library.add(1, "Two Formats")
    pdf.with_suffix(".epub").write_bytes(b"epub")
    con = sqlite3.connect(library.db)
    con.execute(
        "INSERT INTO data (book, format, uncompressed_size, name) VALUES (1,'EPUB',4,?)",
        (pdf.stem,),
    )
    con.commit()
    con.close()

    (row,) = _json_out(capsys, ["--json", "books"])
    assert row["formats"] == ["EPUB", "PDF"]
    assert [str(p) for p in load_books()[0].formats] != row["formats"]  # the library keeps paths


def test_authors_is_the_ampersand_joined_string(library, capsys):
    """A string, not a list: the house convention, `Book.authors_str`, and what
    every consumer of this field renders or splits."""
    library.add(1, "Collaboration", authors="First Person & Second Person")
    (row,) = _json_out(capsys, ["--json", "books"])
    assert row["authors"] == "First Person & Second Person"


def test_books_is_ordered_by_title(library, capsys):
    """Ordering is presentation, so it happens in this layer rather than in SQL —
    but it does have to happen: an unordered list makes every diff of this output
    unreadable."""
    library.add(3, "Zebra")
    library.add(1, "apple")
    library.add(2, "Mango")
    rows = _json_out(capsys, ["--json", "books"])
    assert [r["title"] for r in rows] == ["apple", "Mango", "Zebra"]


def test_books_reports_every_record_the_library_holds(library, capsys):
    """Parity with the package. If these ever disagree, the CLI has grown a
    Calibre opinion of its own, which is the one thing it must not do."""
    for i in range(1, 6):
        library.add(i, f"Book {i}")
    rows = _json_out(capsys, ["--json", "books"])
    assert {r["id"] for r in rows} == {b.id for b in load_books()}


# --------------------------------------------------------------------------
# resolve -- path -> record, batched
# --------------------------------------------------------------------------

def test_resolve_pairs_each_path_with_its_record(library, capsys):
    staged = library.add(42, "Resolvable", authors="A Writer")
    (entry,) = _json_out(capsys, ["--json", "resolve", str(staged)])
    assert entry["path"] == str(staged)
    assert entry["book"]["id"] == 42
    assert entry["book"]["formats"] == ["PDF"]


def test_resolve_returns_null_for_a_path_outside_the_library(library, tmp_path, capsys):
    """Null, and exit 0. The plugin skips foreign PDFs open in Skim; a non-zero
    exit would turn "you also have a paper open" into a failed capture."""
    library.add(1, "A Book")
    foreign = tmp_path / "Downloads" / "Paper (1)" / "paper.pdf"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"%PDF-1.4\n")
    code, out, _ = _run(capsys, ["--json", "resolve", str(foreign)])
    assert code == 0
    assert json.loads(out) == [{"path": str(foreign), "book": None}]


def test_resolve_keeps_argument_order_across_a_batch(library, tmp_path, capsys):
    """The plugin sends Skim's window STACKING order in one call and pairs the
    answers back positionally — frontmost first is the whole point of that list, so
    a reordered or compacted reply would relabel every document."""
    first = library.add(1, "First Book")
    second = library.add(2, "Second Book")
    outside = tmp_path / "Loose (9)" / "loose.pdf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"%PDF-1.4\n")

    entries = _json_out(capsys, ["--json", "resolve", str(second), str(outside), str(first)])
    assert [e["path"] for e in entries] == [str(second), str(outside), str(first)]
    assert [e["book"] and e["book"]["id"] for e in entries] == [2, None, 1]


# --------------------------------------------------------------------------
# argument handling
# --------------------------------------------------------------------------

def test_json_is_honoured_after_the_subcommand_too(library, capsys):
    """An argparse trap with teeth: a subparser writes its own defaults into the
    same namespace, so `--json` declared on the child with `default=False` would
    silently overwrite the `--json` typed before the subcommand — and the caller
    gets a human table where it expected JSON."""
    library.add(1, "A Book")
    assert _json_out(capsys, ["books", "--json"]) == _json_out(capsys, ["--json", "books"])


def test_library_argument_overrides_the_environment(tmp_path, library_at, guard_real_library,
                                                    capsys):
    """The plugin has its own library setting, so `--library` has to win over
    whatever `CALIBRE_LIBRARY` says — here it says a path that does not exist."""
    other = library_at(tmp_path / "Elsewhere")
    other.add(8, "Somewhere Else")
    rows = _json_out(capsys, ["--library", str(other.root), "--json", "books"])
    assert [r["id"] for r in rows] == [8]


def test_a_missing_library_exits_2_with_the_path_on_stderr(guard_real_library, capsys):
    """Non-zero and diagnosable, matching `calfuzz`. A typo'd path must not read as
    an empty library — the plugin would report "no books found" and the user would
    go looking at Calibre."""
    code, out, err = _run(capsys, ["--json", "books"])
    assert code == 2
    assert out == ""
    assert "metadata.db" in err and "definitely-not-a-library" in err


def test_human_output_needs_no_json_flag(library, capsys):
    """The default is for a person at a terminal; `--json` is the machine path."""
    library.add(1, "A Readable Book", authors="A Writer")
    code, out, _ = _run(capsys, ["books"])
    assert code == 0
    assert "A Readable Book" in out and "PDF" in out
    assert not out.startswith("[")


# --------------------------------------------------------------------------
# audit / metadata-candidates -- the two tools that moved in from omni-rag
# --------------------------------------------------------------------------

def test_audit_writes_both_report_files_and_prints_where(library, capsys, tmp_path):
    """The stdout summary carries the PATHS. A report whose location you have to
    guess is a report you regenerate."""
    library.add(1, "Microsoft Word - x.doc", authors="Unknown")
    out = _json_out(capsys, ["--json", "audit", "--out-dir", str(tmp_path / "reports")])
    assert Path(out["json"]).exists()
    assert Path(out["markdown"]).exists()
    assert out["books"] == 1
    assert out["issue_counts"]["junk-title"] == 1


def test_audit_honours_library_like_every_other_subcommand(library_at, capsys, tmp_path, guard_real_library):
    """`--library` is threaded through `CALIBRE_LIBRARY`, so it must reach the
    audit too -- `guard_real_library` points the env at nothing, so a subcommand
    that ignored the flag would fail rather than quietly read the live library."""
    other = library_at(tmp_path / "Elsewhere")
    other.add(1, "Only Book")
    out = _json_out(
        capsys,
        ["--json", "--library", str(other.root), "audit", "--out-dir", str(tmp_path / "r")],
    )
    assert out["books"] == 1


def test_audit_does_not_scan_pdfs_unless_asked(library, capsys, tmp_path):
    library.add(1, "No ISBN", fmt="PDF")
    out = _json_out(capsys, ["--json", "audit", "--out-dir", str(tmp_path / "r")])
    assert out["scanned_pdf_candidates"] == 0


def test_two_audits_do_not_overwrite_each_other(library, capsys, tmp_path, monkeypatch):
    """Timestamped filenames. Comparing today's audit to the last one is the
    point, and a fixed name destroys exactly that."""
    library.add(1, "A Book")
    stamps = iter(["20260101-000000", "20260102-000000"])
    monkeypatch.setattr(
        "calibre_core.cli._stamped",
        lambda d, stem: (
            d.mkdir(parents=True, exist_ok=True) or (s := next(stamps))
            and (d / f"{stem}-{s}.json", d / f"{stem}-{s}.md")
        ),
    )
    a = _json_out(capsys, ["--json", "audit", "--out-dir", str(tmp_path / "r")])
    b = _json_out(capsys, ["--json", "audit", "--out-dir", str(tmp_path / "r")])
    assert a["json"] != b["json"]
    assert len(list((tmp_path / "r").glob("*.json"))) == 2


def test_metadata_candidates_reads_an_audit_json_and_writes_a_report(capsys, tmp_path, monkeypatch):
    """The two commands compose through a FILE, so the expensive audit is run once
    and enriched as often as needed."""
    audit_json = tmp_path / "audit.json"
    audit_json.write_text(json.dumps({
        "safe_existing_isbn_lookup": [
            {"id": 1, "title": "A Book", "authors": "An Author",
             "isbn": "9780262035613", "issues": ["missing-publisher"]}
        ]
    }))

    class Fake:
        def __init__(self, timeout): pass
        def edition(self, isbn):
            return {"isbn": isbn, "title": "A Book", "authors": ["An Author"]}

    monkeypatch.setattr("calibre_core.openlibrary.OpenLibraryClient", Fake)
    out = _json_out(
        capsys,
        ["--json", "metadata-candidates", str(audit_json), "--out-dir", str(tmp_path / "r")],
    )
    assert out["input_candidates"] == 1
    assert out["strong-candidate"] == 1
    assert Path(out["json"]).exists() and Path(out["markdown"]).exists()


def test_metadata_candidates_progress_goes_to_stderr_not_stdout(capsys, tmp_path, monkeypatch):
    """`--json | jq` must not be fed counters."""
    audit_json = tmp_path / "audit.json"
    audit_json.write_text(json.dumps({
        "safe_existing_isbn_lookup": [
            {"id": 1, "title": "T", "authors": "A", "isbn": "9780262035613", "issues": []}
        ]
    }))

    class Fake:
        def __init__(self, timeout): pass
        def edition(self, isbn): return {"isbn": isbn, "error": "http 404"}

    monkeypatch.setattr("calibre_core.openlibrary.OpenLibraryClient", Fake)
    code, out, err = _run(
        capsys,
        ["--json", "metadata-candidates", str(audit_json), "--out-dir", str(tmp_path / "r")],
    )
    assert code == 0
    json.loads(out)              # stdout is parseable JSON and nothing else
    assert "lookup 1/1" in err


def test_every_registered_subcommand_has_a_handler():
    """A subparser with no entry in the handler dict fails with a KeyError at
    dispatch — AFTER argparse has accepted the command, so `--help` and argument
    validation both pass and the break only shows up on a real invocation.

    Checked by comparing the two literals in the AST. Driving each subcommand
    through `main([name, "--help"])` does NOT test this: `--help` raises SystemExit
    inside `parse_args`, so it returns before the handler dict is ever indexed.
    """
    import ast
    import inspect

    from calibre_core import cli

    tree = ast.parse(inspect.getsource(cli))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    registered = {
        node.args[0].value
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    # The handler dict is the only dict literal in `main` whose keys are all strings.
    handled = next(
        {k.value for k in node.keys}
        for node in ast.walk(fn)
        if isinstance(node, ast.Dict)
        and node.keys
        and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)
    )
    assert registered == handled, f"registered but unhandled: {registered - handled}"
    assert registered == {"books", "resolve", "audit", "metadata-candidates"}
