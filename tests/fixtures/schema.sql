-- Minimum Calibre schema needed by calibre_mcp, transcribed from a real
-- metadata.db (Calibre 9.x) via sqlite_master.
--
-- ⚠ DO NOT build a test library with `calibredb`. Pointed at a hand-made
-- library it treats the directory as uninitialised, rewrites the file into the
-- full 48-table Calibre schema (FTS, tag_browser_* views, triggers) and drops
-- every row you inserted. That is why this file exists.
--
-- Only the tables the package actually queries are here. If a new query touches
-- another table, transcribe it from sqlite_master rather than inventing it --
-- COLLATE NOCASE on the text columns is load-bearing (it is what makes tag and
-- author lookups case-insensitive, matching real behaviour).

CREATE TABLE books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL DEFAULT 'Unknown' COLLATE NOCASE,
    sort         TEXT COLLATE NOCASE,
    timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pubdate      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    series_index REAL NOT NULL DEFAULT 1.0,
    author_sort  TEXT COLLATE NOCASE,
    path         TEXT NOT NULL DEFAULT '',
    uuid         TEXT,
    has_cover    BOOL DEFAULT 0,
    last_modified TIMESTAMP NOT NULL DEFAULT '2000-01-01 00:00:00+00:00'
);

CREATE TABLE authors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    sort TEXT COLLATE NOCASE,
    link TEXT NOT NULL DEFAULT '',
    UNIQUE(name)
);

CREATE TABLE books_authors_link (
    id     INTEGER PRIMARY KEY,
    book   INTEGER NOT NULL,
    author INTEGER NOT NULL,
    UNIQUE(book, author)
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    link TEXT NOT NULL DEFAULT '',
    UNIQUE(name)
);

CREATE TABLE books_tags_link (
    id   INTEGER PRIMARY KEY,
    book INTEGER NOT NULL,
    tag  INTEGER NOT NULL,
    UNIQUE(book, tag)
);

-- Transcribed from the real metadata.db. There is deliberately no
-- `books.publisher` column above, because the real schema has none: the
-- publisher is reachable only through this pair. `UNIQUE(book)` is load-bearing
-- documentation -- a book has at most ONE publisher, unlike tags or authors.
CREATE TABLE publishers (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    sort TEXT COLLATE NOCASE,
    link TEXT NOT NULL DEFAULT '',
    UNIQUE(name)
);

CREATE TABLE books_publishers_link (
    id        INTEGER PRIMARY KEY,
    book      INTEGER NOT NULL,
    publisher INTEGER NOT NULL,
    UNIQUE(book)
);

CREATE TABLE data (
    id                INTEGER PRIMARY KEY,
    book              INTEGER NOT NULL,
    format            TEXT NOT NULL COLLATE NOCASE,
    uncompressed_size INTEGER NOT NULL,
    name              TEXT NOT NULL,
    UNIQUE(book, format)
);

CREATE TABLE identifiers (
    id   INTEGER PRIMARY KEY,
    book INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT 'isbn' COLLATE NOCASE,
    val  TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(book, type)
);

CREATE TABLE custom_columns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    name        TEXT NOT NULL,
    datatype    TEXT NOT NULL,
    mark_for_delete BOOL DEFAULT 0 NOT NULL,
    editable    BOOL DEFAULT 1 NOT NULL,
    display     TEXT DEFAULT '{}' NOT NULL,
    is_multiple BOOL DEFAULT 0 NOT NULL,
    normalized  BOOL NOT NULL,
    UNIQUE(label)
);

-- A custom column materialises as a value table plus a link table, numbered by
-- the custom_columns.id. #dupok is id 2 in the real library; the fixture keeps
-- that numbering so the SQL under test is exercised as written.
CREATE TABLE custom_column_2 (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL COLLATE NOCASE,
    link  TEXT NOT NULL DEFAULT "",
    UNIQUE(value)
);

CREATE TABLE books_custom_column_2_link (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    book  INTEGER NOT NULL,
    value INTEGER NOT NULL,
    UNIQUE(book, value)
);
