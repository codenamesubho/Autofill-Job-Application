"""SQLite schema and connection handling (PLAN.md section 4).

SQLite holds the queue, the answer cache and idempotency keys. Anything a human
reads lives in files under artifacts/ instead.

`init_db` is idempotent - calling it repeatedly is the normal path, not an error.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# 2: v2 generic-path schema - adds site_memory, questions.widget and
# answer_cache.jd_dependent, drops applications.ats. CREATE TABLE IF NOT EXISTS
# will not add columns to a database created at version 1, so a stale db is a
# real (if currently unlikely) failure mode. There is no migration runner yet;
# the version is recorded so the mismatch is detectable rather than silent.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id            TEXT PRIMARY KEY,
    job_url       TEXT NOT NULL,
    canonical_url TEXT,
    company       TEXT,
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'PENDING',
    attempt       INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    artifact_dir  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_canonical
    ON applications(canonical_url);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    field_id       TEXT NOT NULL,
    label          TEXT NOT NULL,
    type           TEXT NOT NULL,
    widget         TEXT,
    answer         TEXT,
    source         TEXT,
    confidence     REAL,
    flagged        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(application_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_questions_app ON questions(application_id);

CREATE TABLE IF NOT EXISTS answer_cache (
    question_hash TEXT PRIMARY KEY,
    label_sample  TEXT NOT NULL,
    type          TEXT NOT NULL,
    jd_dependent  INTEGER NOT NULL DEFAULT 0,
    answer        TEXT NOT NULL,
    uses          INTEGER NOT NULL DEFAULT 0,
    last_used     TEXT
);

-- Learned knowledge about a page, produced by the generic machinery at runtime.
-- This is what replaces hand-written per-vendor adapters: the second visit to a
-- domain replays the strategies that worked, with no vendor code to maintain.
CREATE TABLE IF NOT EXISTS site_memory (
    domain                 TEXT NOT NULL,
    page_fingerprint       TEXT NOT NULL,
    apply_entry_selector   TEXT,
    extraction_tier_used   INTEGER,
    widget_strategies_json TEXT,
    field_id_map_json      TEXT,
    successes              INTEGER NOT NULL DEFAULT 0,
    failures               INTEGER NOT NULL DEFAULT 0,
    updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (domain, page_fingerprint)
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT REFERENCES applications(id) ON DELETE CASCADE,
    ts             TEXT NOT NULL DEFAULT (datetime('now')),
    kind           TEXT NOT NULL,
    payload_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_app_ts ON events(application_id, ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Create the schema if absent. Safe to call on every startup."""
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
    return conn


@contextmanager
def session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()
