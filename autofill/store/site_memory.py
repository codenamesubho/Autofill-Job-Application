"""Learned per-page knowledge - the replacement for hand-written ATS adapters.

An adapter is knowledge about a vendor, written by a human, maintained forever.
Site memory is knowledge about a *page*, produced by the generic machinery at
runtime and invalidated automatically when it stops working.

The record is advisory. A caller must always be able to fall back to a full
generic extraction if a remembered strategy fails - which is what `record_failure`
plus `is_trusted` are for.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class SiteMemory(BaseModel):
    domain: str
    page_fingerprint: str
    apply_entry_selector: str | None = None
    extraction_tier_used: int | None = None
    widget_strategies: dict[str, Any] = Field(default_factory=dict)
    field_id_map: dict[str, str] = Field(default_factory=dict)
    successes: int = 0
    failures: int = 0

    @property
    def is_trusted(self) -> bool:
        """Replay remembered strategies only while they keep working.

        One failure is noise - a slow page, a transient overlay. A record that
        fails as often as it succeeds is worse than no record, because it sends
        the run down a path that then has to be unwound.
        """
        return self.successes > self.failures


class SiteMemoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self, domain: str, page_fingerprint: str) -> SiteMemory | None:
        row = self.conn.execute(
            "SELECT * FROM site_memory WHERE domain = ? AND page_fingerprint = ?",
            (domain, page_fingerprint),
        ).fetchone()
        if row is None:
            return None
        return SiteMemory(
            domain=row["domain"],
            page_fingerprint=row["page_fingerprint"],
            apply_entry_selector=row["apply_entry_selector"],
            extraction_tier_used=row["extraction_tier_used"],
            widget_strategies=json.loads(row["widget_strategies_json"] or "{}"),
            field_id_map=json.loads(row["field_id_map_json"] or "{}"),
            successes=row["successes"],
            failures=row["failures"],
        )

    def remember(self, mem: SiteMemory) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO site_memory(domain, page_fingerprint, apply_entry_selector,
                                        extraction_tier_used, widget_strategies_json,
                                        field_id_map_json, successes, failures, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(domain, page_fingerprint) DO UPDATE SET
                    apply_entry_selector   = excluded.apply_entry_selector,
                    extraction_tier_used   = excluded.extraction_tier_used,
                    widget_strategies_json = excluded.widget_strategies_json,
                    field_id_map_json      = excluded.field_id_map_json,
                    updated_at             = datetime('now')
                """,
                (
                    mem.domain,
                    mem.page_fingerprint,
                    mem.apply_entry_selector,
                    mem.extraction_tier_used,
                    json.dumps(mem.widget_strategies),
                    json.dumps(mem.field_id_map),
                    mem.successes,
                    mem.failures,
                ),
            )

    def record_success(self, domain: str, page_fingerprint: str) -> None:
        self._bump(domain, page_fingerprint, "successes")

    def record_failure(self, domain: str, page_fingerprint: str) -> None:
        self._bump(domain, page_fingerprint, "failures")

    def _bump(self, domain: str, page_fingerprint: str, column: str) -> None:
        assert column in {"successes", "failures"}  # never interpolate user input
        with self.conn:
            self.conn.execute(
                f"UPDATE site_memory SET {column} = {column} + 1, "
                "updated_at = datetime('now') "
                "WHERE domain = ? AND page_fingerprint = ?",
                (domain, page_fingerprint),
            )

    def forget(self, domain: str, page_fingerprint: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM site_memory WHERE domain = ? AND page_fingerprint = ?",
                (domain, page_fingerprint),
            )

    def size(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM site_memory").fetchone()["n"]
