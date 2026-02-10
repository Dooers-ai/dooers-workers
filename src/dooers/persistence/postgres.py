import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from dooers.protocol.models import DocumentPart, ImagePart, Run, TextPart, Thread, ThreadEvent


class PostgresPersistence:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        database: str,
        password: str,
        ssl: bool = False,
        table_prefix: str = "worker_",
    ):
        self._host = host
        self._port = port
        self._user = user
        self._database = database
        self._password = password
        self._ssl = ssl
        self._prefix = table_prefix
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            database=self._database,
            password=self._password,
            ssl="require" if self._ssl else None,
        )

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()

    async def migrate(self) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        threads_table = f"{self._prefix}threads"
        events_table = f"{self._prefix}events"
        runs_table = f"{self._prefix}runs"

        async with self._pool.acquire() as conn:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {threads_table} (
                    id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    user_id TEXT,
                    title TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    last_event_at TIMESTAMPTZ NOT NULL
                )
            """)

            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {events_table} (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES {threads_table}(id),
                    run_id TEXT,
                    type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    user_id TEXT,
                    user_name TEXT,
                    user_email TEXT,
                    content JSONB,
                    data JSONB,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)

            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {runs_table} (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES {threads_table}(id),
                    agent_id TEXT,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ,
                    error TEXT
                )
            """)

            # Incremental migrations for columns added after initial release
            await conn.execute(f"""
                ALTER TABLE {events_table} ADD COLUMN IF NOT EXISTS run_id TEXT
            """)
            await conn.execute(f"""
                ALTER TABLE {events_table} ADD COLUMN IF NOT EXISTS user_name TEXT
            """)
            await conn.execute(f"""
                ALTER TABLE {events_table} ADD COLUMN IF NOT EXISTS user_email TEXT
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_worker_id
                    ON {threads_table}(worker_id)
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}threads_user_id
                    ON {threads_table}(user_id)
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}events_thread_id
                    ON {events_table}(thread_id)
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}events_user_id
                    ON {events_table}(user_id)
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}runs_thread_id
                    ON {runs_table}(thread_id)
            """)

            settings_table = f"{self._prefix}settings"
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {settings_table} (
                    worker_id TEXT PRIMARY KEY,
                    values JSONB NOT NULL DEFAULT '{{}}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._prefix}settings_worker
                    ON {settings_table}(worker_id)
            """)

    async def create_thread(self, thread: Thread) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {table} (id, worker_id, user_id, title, created_at, updated_at, last_event_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                thread.id,
                thread.worker_id,
                thread.user_id,
                thread.title,
                thread.created_at,
                thread.updated_at,
                thread.last_event_at,
            )

    async def get_thread(self, thread_id: str) -> Thread | None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {table} WHERE id = $1",
                thread_id,
            )

        if not row:
            return None

        return Thread(
            id=row["id"],
            worker_id=row["worker_id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_event_at=row["last_event_at"],
        )

    async def update_thread(self, thread: Thread) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                SET user_id = $1, title = $2, updated_at = $3, last_event_at = $4
                WHERE id = $5
                """,
                thread.user_id,
                thread.title,
                thread.updated_at,
                thread.last_event_at,
                thread.id,
            )

    async def delete_thread(self, thread_id: str) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        events_table = f"{self._prefix}events"
        runs_table = f"{self._prefix}runs"
        threads_table = f"{self._prefix}threads"

        async with self._pool.acquire() as conn:
            await conn.execute(f"DELETE FROM {events_table} WHERE thread_id = $1", thread_id)
            await conn.execute(f"DELETE FROM {runs_table} WHERE thread_id = $1", thread_id)
            await conn.execute(f"DELETE FROM {threads_table} WHERE id = $1", thread_id)

    async def list_threads(
        self,
        worker_id: str,
        user_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[Thread]:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}threads"
        async with self._pool.acquire() as conn:
            if user_id:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {table}
                    WHERE worker_id = $1 AND user_id = $2
                    ORDER BY last_event_at DESC
                    LIMIT $3
                    """,
                    worker_id,
                    user_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {table}
                    WHERE worker_id = $1
                    ORDER BY last_event_at DESC
                    LIMIT $2
                    """,
                    worker_id,
                    limit,
                )

        return [
            Thread(
                id=row["id"],
                worker_id=row["worker_id"],
                user_id=row["user_id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                last_event_at=row["last_event_at"],
            )
            for row in rows
        ]

    async def create_event(self, event: ThreadEvent) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}events"
        content_json = None
        if event.content:
            content_json = json.dumps([self._serialize_content_part(p) for p in event.content])

        data_json = json.dumps(event.data) if event.data else None

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {table} (id, thread_id, run_id, type, actor, user_id, user_name, user_email, content, data, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                event.id,
                event.thread_id,
                event.run_id,
                event.type,
                event.actor,
                event.user_id,
                event.user_name,
                event.user_email,
                content_json,
                data_json,
                event.created_at,
            )

    async def get_events(
        self,
        thread_id: str,
        *,
        after_event_id: str | None = None,
        limit: int = 50,
        order: str = "asc",
        filters: dict[str, str] | None = None,
    ) -> list[ThreadEvent]:
        if not self._pool:
            raise RuntimeError("Not connected")

        from dooers.persistence.base import FILTERABLE_FIELDS

        table = f"{self._prefix}events"
        conditions = ["thread_id = $1"]
        params: list[Any] = [thread_id]
        idx = 2

        if after_event_id:
            async with self._pool.acquire() as conn:
                ref_row = await conn.fetchrow(f"SELECT created_at FROM {table} WHERE id = $1", after_event_id)
            if ref_row:
                op = "<" if order == "desc" else ">"
                conditions.append(f"created_at {op} ${idx}")
                params.append(ref_row["created_at"])
                idx += 1

        if filters:
            for key, value in filters.items():
                if key in FILTERABLE_FIELDS:
                    conditions.append(f"{key} = ${idx}")
                    params.append(value)
                    idx += 1

        direction = "DESC" if order == "desc" else "ASC"
        where = " AND ".join(conditions)
        params.append(limit)

        query = f"SELECT * FROM {table} WHERE {where} ORDER BY created_at {direction} LIMIT ${idx}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: asyncpg.Record) -> ThreadEvent:
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
            user_id=row["user_id"],
            user_name=row["user_name"],
            user_email=row["user_email"],
            content=content,
            data=data,
            created_at=row["created_at"],
        )

    async def create_run(self, run: Run) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}runs"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {table} (id, thread_id, agent_id, status, started_at, ended_at, error)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                run.id,
                run.thread_id,
                run.agent_id,
                run.status,
                run.started_at,
                run.ended_at,
                run.error,
            )

    async def update_run(self, run: Run) -> None:
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}runs"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                SET agent_id = $1, status = $2, ended_at = $3, error = $4
                WHERE id = $5
                """,
                run.agent_id,
                run.status,
                run.ended_at,
                run.error,
                run.id,
            )

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
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT values FROM {table} WHERE worker_id = $1",
                worker_id,
            )

        if not row:
            return {}

        values = row["values"]
        # asyncpg returns JSONB as dict directly
        if isinstance(values, str):
            return json.loads(values)
        return values

    async def update_setting(self, worker_id: str, field_id: str, value: Any) -> datetime:
        """Update a single field value. Returns updated_at timestamp."""
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        now = datetime.now(UTC)

        # Get existing values
        current_values = await self.get_settings(worker_id)
        current_values[field_id] = value
        values_json = json.dumps(current_values)

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {table} (worker_id, values, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(worker_id) DO UPDATE SET
                    values = EXCLUDED.values,
                    updated_at = EXCLUDED.updated_at
                """,
                worker_id,
                values_json,
                now,
                now,
            )
        return now

    async def set_settings(self, worker_id: str, values: dict[str, Any]) -> datetime:
        """Replace all settings values. Returns updated_at timestamp."""
        if not self._pool:
            raise RuntimeError("Not connected")

        table = f"{self._prefix}settings"
        now = datetime.now(UTC)
        values_json = json.dumps(values)

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {table} (worker_id, values, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(worker_id) DO UPDATE SET
                    values = EXCLUDED.values,
                    updated_at = EXCLUDED.updated_at
                """,
                worker_id,
                values_json,
                now,
                now,
            )
        return now
