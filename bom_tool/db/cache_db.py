from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from bom_tool.models import SearchType

T = TypeVar("T")


class CacheDB:
    def __init__(self, db_path: str | Path = "bom_cache.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def get(
        self,
        supplier: str,
        search_type: SearchType,
        keyword: str,
        ttl_hours: int = 24,
    ) -> dict[str, Any] | None:
        row = self._execute(
            """
            SELECT payload, fetched_at
            FROM query_cache
            WHERE supplier = ? AND search_type = ? AND keyword = ?
            """,
            (supplier, search_type.value, keyword),
        ).fetchone()

        if not row:
            return None

        fetched_at = datetime.fromisoformat(row[1])
        if fetched_at + timedelta(hours=ttl_hours) < datetime.now(timezone.utc):
            return None

        return json.loads(row[0])

    def get_many(
        self,
        keys: list[tuple[str, SearchType, str]],
        ttl_hours: int = 24,
    ) -> dict[tuple[str, SearchType, str], dict[str, Any]]:
        if not keys:
            return {}

        now = datetime.now(timezone.utc)
        results: dict[tuple[str, SearchType, str], dict[str, Any]] = {}
        grouped: dict[tuple[str, str], list[str]] = {}
        for supplier, search_type, keyword in keys:
            grouped.setdefault((supplier, search_type.value), []).append(keyword)

        for (supplier, search_type_value), keywords in grouped.items():
            for chunk in _chunks(list(dict.fromkeys(keywords)), 500):
                placeholders = ", ".join("?" for _ in chunk)
                rows = self._execute(
                    f"""
                    SELECT keyword, payload, fetched_at
                    FROM query_cache
                    WHERE supplier = ? AND search_type = ? AND keyword IN ({placeholders})
                    """,
                    (supplier, search_type_value, *chunk),
                ).fetchall()

                search_type = SearchType(search_type_value)
                for keyword, payload, fetched_at_value in rows:
                    fetched_at = datetime.fromisoformat(fetched_at_value)
                    if fetched_at + timedelta(hours=ttl_hours) < now:
                        continue
                    results[(supplier, search_type, keyword)] = json.loads(payload)

        return results

    def set(
        self,
        supplier: str,
        search_type: SearchType,
        keyword: str,
        payload: dict[str, Any],
    ) -> None:
        fetched_at = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO query_cache (supplier, search_type, keyword, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(supplier, search_type, keyword)
            DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at
            """,
            (
                supplier,
                search_type.value,
                keyword,
                json.dumps(payload, ensure_ascii=False),
                fetched_at,
            ),
        )
        self._commit()

    def set_many(
        self,
        rows: list[tuple[str, SearchType, str, dict[str, Any]]],
    ) -> None:
        if not rows:
            return

        fetched_at = datetime.now(timezone.utc).isoformat()
        self._get_connection().executemany(
            """
            INSERT INTO query_cache (supplier, search_type, keyword, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(supplier, search_type, keyword)
            DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at
            """,
            [
                (
                    supplier,
                    search_type.value,
                    keyword,
                    json.dumps(payload, ensure_ascii=False),
                    fetched_at,
                )
                for supplier, search_type, keyword, payload in rows
            ],
        )
        self._commit()

    def clear(self) -> None:
        self._execute("DELETE FROM query_cache")
        self._commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        conn = self._get_connection()
        return conn.execute(sql, params)

    def _commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()
            self._conn.commit()
        return self._conn

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_cache (
                supplier TEXT NOT NULL,
                search_type TEXT NOT NULL,
                keyword TEXT NOT NULL,
                payload TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (supplier, search_type, keyword)
            )
            """
        )


def _chunks(values: list[T], size: int) -> list[list[T]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
