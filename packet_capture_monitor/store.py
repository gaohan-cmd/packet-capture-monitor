from __future__ import annotations

import copy
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_db_path() -> Path:
    configured = os.environ.get("CAPTURE_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / ".data" / "captures.sqlite3"


class CaptureStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    method TEXT,
                    url TEXT,
                    host TEXT,
                    path TEXT,
                    status_code INTEGER,
                    content_type TEXT,
                    duration_ms REAL,
                    state TEXT NOT NULL,
                    response_size INTEGER,
                    record_json TEXT NOT NULL
                )
                """
            )
            self._ensure_column(connection, "captures", "user_id", "TEXT NOT NULL DEFAULT 'default'")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_captures_user_updated_at ON captures(user_id, updated_at DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_captures_host ON captures(host)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_captures_status ON captures(status_code)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                )
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def migrate_legacy_user_id(self, user_id: str) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE captures SET user_id = ? WHERE user_id = 'default'",
                    (user_id,),
                )
                rows = connection.execute("SELECT key, value, updated_at FROM settings").fetchall()
                for row in rows:
                    connection.execute(
                        """
                        INSERT INTO user_settings (user_id, key, value, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, key) DO NOTHING
                        """,
                        (user_id, row["key"], row["value"], row["updated_at"]),
                    )

    def upsert_event(self, user_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        capture_id = event.get("capture_id")
        if not capture_id:
            raise ValueError("capture_id is required")

        timestamp = event.get("timestamp") or utc_now()
        event_type = event.get("type")

        with self._lock:
            current = self.get(user_id, capture_id, default=None)
            record = current or {
                "id": capture_id,
                "user_id": user_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "state": "pending",
            }
            record["user_id"] = user_id

            if event_type == "request":
                request_data = event.get("request") or {}
                record["request"] = request_data
                record["method"] = request_data.get("method")
                record["scheme"] = request_data.get("scheme")
                record["host"] = request_data.get("host")
                record["path"] = request_data.get("path")
                record["url"] = request_data.get("url")
                record["state"] = "pending"
                record.setdefault("started_at", timestamp)
            elif event_type == "response":
                response_data = event.get("response") or {}
                record["response"] = response_data
                record["status_code"] = response_data.get("status_code")
                record["duration_ms"] = response_data.get("duration_ms")
                body = response_data.get("body") or {}
                record["content_type"] = body.get("content_type") or self._header_value(
                    response_data.get("headers") or [], "content-type"
                )
                record["response_size"] = body.get("size")
                record["completed_at"] = timestamp
                record["state"] = "complete"
            elif event_type == "error":
                record["error"] = event.get("error") or "Unknown proxy error"
                record["state"] = "error"
                record["completed_at"] = timestamp
            else:
                raise ValueError("type must be request, response, or error")

            record["updated_at"] = timestamp
            self._save(user_id, record)
            return record

    def _save(self, user_id: str, record: Dict[str, Any]) -> None:
        response = record.get("response") or {}
        body = response.get("body") or {}
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT user_id FROM captures WHERE id = ?",
                (record["id"],),
            ).fetchone()
            if existing and existing["user_id"] != user_id:
                raise ValueError("capture_id already belongs to another user")
            connection.execute(
                """
                INSERT INTO captures (
                    id, user_id, created_at, updated_at, method, url, host, path,
                    status_code, content_type, duration_ms, state, response_size, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    updated_at=excluded.updated_at,
                    method=excluded.method,
                    url=excluded.url,
                    host=excluded.host,
                    path=excluded.path,
                    status_code=excluded.status_code,
                    content_type=excluded.content_type,
                    duration_ms=excluded.duration_ms,
                    state=excluded.state,
                    response_size=excluded.response_size,
                    record_json=excluded.record_json
                """,
                (
                    record["id"],
                    user_id,
                    record.get("created_at") or utc_now(),
                    record.get("updated_at") or utc_now(),
                    record.get("method"),
                    record.get("url"),
                    record.get("host"),
                    record.get("path"),
                    record.get("status_code"),
                    record.get("content_type") or body.get("content_type"),
                    record.get("duration_ms"),
                    record.get("state", "pending"),
                    record.get("response_size") or body.get("size"),
                    json.dumps(record, ensure_ascii=False),
                ),
            )

    def get(self, user_id: str, capture_id: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM captures WHERE id = ? AND user_id = ?",
                (capture_id, user_id),
            ).fetchone()
        if not row:
            return default
        return json.loads(row["record_json"])

    def list(
        self,
        user_id: str,
        limit: int = 200,
        query: str = "",
        method: str = "",
        status: str = "",
    ) -> List[Dict[str, Any]]:
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if query:
            clauses.append("(url LIKE ? OR path LIKE ? OR host LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if method:
            clauses.append("method = ?")
            params.append(method.upper())
        if status:
            if status == "pending":
                clauses.append("state = ?")
                params.append("pending")
            elif status == "error":
                clauses.append("state = ?")
                params.append("error")
            elif status.endswith("xx") and status[0].isdigit():
                start = int(status[0]) * 100
                clauses.append("status_code >= ? AND status_code < ?")
                params.extend([start, start + 100])

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 1000)))
        sql = f"SELECT record_json FROM captures {where} ORDER BY updated_at DESC LIMIT ?"

        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.summarize(json.loads(row["record_json"])) for row in rows]

    def clear(self, user_id: str) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM captures WHERE user_id = ?", (user_id,))

    def get_setting(self, user_id: str, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
                (user_id, key),
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def set_setting(self, user_id: str, key: str, value: Any) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO user_settings (user_id, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (user_id, key, json.dumps(value, ensure_ascii=False), utc_now()),
                )

    def stats(self, user_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) AS ok,
                    SUM(CASE WHEN status_code BETWEEN 400 AND 599 THEN 1 ELSE 0 END) AS failed,
                    AVG(duration_ms) AS avg_duration_ms,
                    SUM(response_size) AS total_response_bytes
                FROM captures
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return {
            "total": rows["total"] or 0,
            "ok": rows["ok"] or 0,
            "failed": rows["failed"] or 0,
            "avg_duration_ms": round(rows["avg_duration_ms"] or 0, 2),
            "total_response_bytes": rows["total_response_bytes"] or 0,
        }

    @staticmethod
    def summarize(record: Dict[str, Any]) -> Dict[str, Any]:
        summary = copy.deepcopy(record)
        for section in ("request", "response"):
            body = (summary.get(section) or {}).get("body")
            if body:
                if body.get("text") is not None:
                    text = body["text"]
                    body["text"] = text[:240] + ("..." if len(text) > 240 else "")
                if body.get("base64") is not None:
                    body["base64"] = ""
                if body.get("hex") is not None:
                    body["hex"] = body["hex"][:256]
        return summary

    @staticmethod
    def _header_value(headers: List[Dict[str, str]], name: str) -> str:
        lowered = name.lower()
        for header in headers:
            if header.get("name", "").lower() == lowered:
                return header.get("value", "")
        return ""
