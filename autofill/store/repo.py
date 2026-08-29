"""Repositories over the SQLite tables. No business logic beyond persistence."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from autofill.models import AppState
from autofill.orchestrator.states import Status, assert_transition


class ApplicationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, app: AppState) -> AppState:
        """Insert, or return the existing row for the same canonical URL.

        Idempotent by canonical_url so re-running a job list never duplicates work.
        """
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO applications
                    (id, job_url, canonical_url, company, title, ats, status,
                     attempt, last_error, artifact_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_url) DO NOTHING
                """,
                (
                    app.id,
                    app.job_url,
                    app.canonical_url or app.job_url,
                    app.company,
                    app.title,
                    app.ats,
                    app.status,
                    app.attempt,
                    app.last_error,
                    app.artifact_dir,
                ),
            )
        row = self.conn.execute(
            "SELECT * FROM applications WHERE canonical_url = ?",
            (app.canonical_url or app.job_url,),
        ).fetchone()
        return AppState(**dict(row))

    def get(self, app_id: str) -> AppState | None:
        row = self.conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        return AppState(**dict(row)) if row else None

    def list(self, status: Status | None = None) -> list[AppState]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM applications ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM applications WHERE status = ? ORDER BY created_at",
                (status.value,),
            ).fetchall()
        return [AppState(**dict(r)) for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()
        return dict(Counter({r["status"]: r["n"] for r in rows}))

    def set_status(
        self, app_id: str, to: Status, *, error: str | None = None
    ) -> None:
        """Transition an application, refusing moves the state machine forbids."""
        row = self.conn.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such application: {app_id}")
        assert_transition(Status(row["status"]), to)
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = ?, last_error = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (to.value, error, app_id),
            )


class AnswerCacheRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, question_hash: str) -> str | None:
        row = self.conn.execute(
            "SELECT answer FROM answer_cache WHERE question_hash = ?", (question_hash,)
        ).fetchone()
        if row is None:
            return None
        with self.conn:
            self.conn.execute(
                "UPDATE answer_cache SET uses = uses + 1, "
                "last_used = datetime('now') WHERE question_hash = ?",
                (question_hash,),
            )
        return row["answer"]

    def put(self, question_hash: str, label_sample: str, type_: str, answer: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO answer_cache(question_hash, label_sample, type, answer,
                                         uses, last_used)
                VALUES (?, ?, ?, ?, 0, datetime('now'))
                ON CONFLICT(question_hash) DO UPDATE SET
                    answer = excluded.answer, last_used = datetime('now')
                """,
                (question_hash, label_sample, type_, answer),
            )

    def size(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM answer_cache").fetchone()["n"]


class EventRepo:
    """Append-only audit trail. Every interesting step writes one row."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log(self, application_id: str | None, kind: str, **payload: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO events(application_id, kind, payload_json) VALUES (?, ?, ?)",
                (application_id, kind, json.dumps(payload, default=str)),
            )

    def for_application(self, application_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE application_id = ? ORDER BY id",
            (application_id,),
        ).fetchall()
        return [dict(r) for r in rows]
