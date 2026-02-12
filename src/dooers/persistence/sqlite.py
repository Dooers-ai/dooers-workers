import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from dooers.protocol.models import DocumentPart, ImagePart, Run, TextPart, Thread, ThreadEvent


class SqlitePersistence:
    def __init__(self, *, database_name: str, table_prefix: str = "worker_"):
        self._database_path = database_name.replace("sqlite:///", "")
        self._prefix = table_prefix
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._database_path)
        self._conn.row_factory = aiosqlite.Row

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()

    async def migrate(self) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        threads_table = f"{self._prefix}threads"
        events_table = f"{self._prefix}events"
        runs_table = f"{self._prefix}runs"

        await self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS {threads_table} (
                id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_event_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS {events_table} (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                run_id TEXT,
                type TEXT NOT NULL,
                actor TEXT NOT NULL,
                author TEXT,
                user_id TEXT,
                user_name TEXT,
                user_email TEXT,
                content TEXT,
                data TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES {threads_table}(id)
            );

            CREATE TABLE IF NOT EXISTS {runs_table} (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                agent_id TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                error TEXT,
                FOREIGN KEY (thread_id) REFERENCES {threads_table}(id)
            );

            CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_worker_id
                ON {threads_table}(worker_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_user_id
                ON {threads_table}(user_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_organization_id
                ON {threads_table}(organization_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_workspace_id
                ON {threads_table}(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}events_thread_id
                ON {events_table}(thread_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}events_user_id
                ON {events_table}(user_id);
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}runs_thread_id
                ON {runs_table}(thread_id);

            CREATE TABLE IF NOT EXISTS {self._prefix}settings (
                worker_id TEXT PRIMARY KEY,
                values TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_{self._prefix}settings_worker
                ON {self._prefix}settings(worker_id);
        """)
        await self._conn.commit()

    async def create_thread(self, thread: Thread) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        await self._conn.execute(
            f"""
            INSERT INTO {table} (id, worker_id, organization_id, workspace_id, user_id, title, created_at, updated_at, last_event_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread.id,
                thread.worker_id,
                thread.organization_id,
                thread.workspace_id,
                thread.user_id,
                thread.title,
                thread.created_at.isoformat(),
                thread.updated_at.isoformat(),
                thread.last_event_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def get_thread(self, thread_id: str) -> Thread | None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        cursor = await self._conn.execute(
            f"SELECT * FROM {table} WHERE id = ?",
            (thread_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return None

        return Thread(
            id=row["id"],
            worker_id=row["worker_id"],
            organization_id=row["organization_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_event_at=datetime.fromisoformat(row["last_event_at"]),
        )

    async def update_thread(self, thread: Thread) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        await self._conn.execute(
            f"""
            UPDATE {table}
            SET organization_id = ?, workspace_id = ?, user_id = ?, title = ?, updated_at = ?, last_event_at = ?
            WHERE id = ?
            """,
            (
                thread.organization_id,
                thread.workspace_id,
                thread.user_id,
                thread.title,
                thread.updated_at.isoformat(),
                thread.last_event_at.isoformat(),
                thread.id,
            ),
        )
        await self._conn.commit()

    async def delete_thread(self, thread_id: str) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        events_table = f"{self._prefix}events"
        runs_table = f"{self._prefix}runs"
        threads_table = f"{self._prefix}threads"

        await self._conn.execute(f"DELETE FROM {events_table} WHERE thread_id = ?", (thread_id,))
        await self._conn.execute(f"DELETE FROM {runs_table} WHERE thread_id = ?", (thread_id,))
        await self._conn.execute(f"DELETE FROM {threads_table} WHERE id = ?", (thread_id,))
        await self._conn.commit()

    async def list_threads(
        self,
        worker_id: str,
        organization_id: str,
        workspace_id: str,
        user_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[Thread]:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        conditions = ["worker_id = ?", "organization_id = ?", "workspace_id = ?"]
        params: list[Any] = [worker_id, organization_id, workspace_id]

        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)

        if cursor:
            conditions.append("last_event_at < ?")
            params.append(cursor)

        params.append(limit)
        where = " AND ".join(conditions)

        query = f"""
            SELECT * FROM {table}
            WHERE {where}
            ORDER BY last_event_at DESC
            LIMIT ?
        """
        cursor_result = await self._conn.execute(query, tuple(params))
        rows = await cursor_result.fetchall()

        return [
            Thread(
                id=row["id"],
                worker_id=row["worker_id"],
                organization_id=row["organization_id"],
                workspace_id=row["workspace_id"],
                user_id=row["user_id"],
                title=row["title"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                last_event_at=datetime.fromisoformat(row["last_event_at"]),
            )
            for row in rows
        ]

    async def create_event(self, event: ThreadEvent) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}events"
        content_json = None
        if event.content:
            content_json = json.dumps([self._serialize_content_part(p) for p in event.content])

        data_json = json.dumps(event.data) if event.data else None

        await self._conn.execute(
            f"""
            INSERT INTO {table} (id, thread_id, run_id, type, actor, author, user_id, user_name, user_email, content, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.thread_id,
                event.run_id,
                event.type,
                event.actor,
                event.author,
                event.user_id,
                event.user_name,
                event.user_email,
                content_json,
                data_json,
                event.created_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def get_events(
        self,
        thread_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 50,
        order: str = "asc",
        filters: dict[str, str] | None = None,
    ) -> list[ThreadEvent]:
        if not self._conn:
            raise RuntimeError("Not connected")

        from dooers.persistence.base import FILTERABLE_FIELDS

        table = f"{self._prefix}events"
        conditions = ["thread_id = ?"]
        params: list[Any] = [thread_id]

        if after_event_id:
            ref = await self._conn.execute(f"SELECT created_at FROM {table} WHERE id = ?", (after_event_id,))
            ref_row = await ref.fetchone()
            if ref_row:
                op = "<" if order == "desc" else ">"
                conditions.append(f"created_at {op} ?")
                params.append(ref_row["created_at"])

        if filters:
            for key, value in filters.items():
                if key in FILTERABLE_FIELDS:
                    conditions.append(f"{key} = ?")
                    params.append(value)

        direction = "DESC" if order == "desc" else "ASC"
        where = " AND ".join(conditions)
        params.append(limit)

        query = f"SELECT * FROM {table} WHERE {where} ORDER BY created_at {direction} LIMIT ?"
        cursor_result = await self._conn.execute(query, tuple(params))
        rows = await cursor_result.fetchall()

        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: aiosqlite.Row) -> ThreadEvent:
        content = None
        if row["content"]:
            content_data = json.loads(row["content"])
            content = [self._deserialize_content_part(p) for p in content_data]

        data = json.loads(row["data"]) if row["data"] else None

        return ThreadEvent(
            id=row["id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            type=row["type"],
            actor=row["actor"],
            author=row["author"] if "author" in row.keys() else None,
            user_id=row["user_id"],
            user_name=row["user_name"],
            user_email=row["user_email"],
            content=content,
            data=data,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    async def create_run(self, run: Run) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}runs"
        await self._conn.execute(
            f"""
            INSERT INTO {table} (id, thread_id, agent_id, status, started_at, ended_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.thread_id,
                run.agent_id,
                run.status,
                run.started_at.isoformat(),
                run.ended_at.isoformat() if run.ended_at else None,
                run.error,
            ),
        )
        await self._conn.commit()

    async def update_run(self, run: Run) -> None:
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}runs"
        await self._conn.execute(
            f"""
            UPDATE {table}
            SET agent_id = ?, status = ?, ended_at = ?, error = ?
            WHERE id = ?
            """,
            (
                run.agent_id,
                run.status,
                run.ended_at.isoformat() if run.ended_at else None,
                run.error,
                run.id,
            ),
        )
        await self._conn.commit()

    def _serialize_content_part(self, part) -> dict:
        if hasattr(part, "model_dump"):
            return part.model_dump()
        return dict(part)

    def _deserialize_content_part(self, data: dict):
        part_type = data.get("type")
        if part_type == "text":
            return TextPart(**data)
        elif part_type == "image":
            return ImagePart(**data)
        elif part_type == "document":
            return DocumentPart(**data)
        return data

    async def get_settings(self, worker_id: str) -> dict[str, Any]:
        """Get all stored values for a worker. Returns empty dict if none."""
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        cursor = await self._conn.execute(
            f"SELECT values FROM {table} WHERE worker_id = ?",
            (worker_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return {}

        return json.loads(row["values"])

    async def update_setting(self, worker_id: str, field_id: str, value: Any) -> datetime:
        """Update a single field value. Returns updated_at timestamp."""
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        now = datetime.now(UTC)
        now_str = now.isoformat()

        # Get existing values
        current_values = await self.get_settings(worker_id)
        current_values[field_id] = value
        values_json = json.dumps(current_values)

        # Upsert
        await self._conn.execute(
            f"""
            INSERT INTO {table} (worker_id, values, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                values = excluded.values,
                updated_at = excluded.updated_at
            """,
            (worker_id, values_json, now_str, now_str),
        )
        await self._conn.commit()
        return now

    async def set_settings(self, worker_id: str, values: dict[str, Any]) -> datetime:
        """Replace all settings values. Returns updated_at timestamp."""
        if not self._conn:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        now = datetime.now(UTC)
        now_str = now.isoformat()
        values_json = json.dumps(values)

        # Upsert
        await self._conn.execute(
            f"""
            INSERT INTO {table} (worker_id, values, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                values = excluded.values,
                updated_at = excluded.updated_at
            """,
            (worker_id, values_json, now_str, now_str),
        )
        await self._conn.commit()
        return now
