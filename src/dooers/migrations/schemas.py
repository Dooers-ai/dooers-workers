from typing import Literal


def get_migration_sql(
    database_type: Literal["postgres", "sqlite"],
    table_prefix: str = "worker_",
) -> str:
    threads_table = f"{table_prefix}threads"
    events_table = f"{table_prefix}events"
    runs_table = f"{table_prefix}runs"
    settings_table = f"{table_prefix}settings"

    if database_type == "postgres":
        return f"""
            CREATE TABLE IF NOT EXISTS {threads_table} (
                id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL,
                user_id TEXT,
                title TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                last_event_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS {events_table} (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES {threads_table}(id),
                run_id TEXT,
                type TEXT NOT NULL,
                actor TEXT NOT NULL,
                author TEXT,
                user_id TEXT,
                user_name TEXT,
                user_email TEXT,
                content JSONB,
                data JSONB,
                created_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS {runs_table} (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES {threads_table}(id),
                agent_id TEXT,
                status TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                ended_at TIMESTAMPTZ,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS {settings_table} (
                worker_id TEXT PRIMARY KEY,
                values JSONB NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_{table_prefix}threads_worker_id
                ON {threads_table}(worker_id);
            CREATE INDEX IF NOT EXISTS idx_{table_prefix}threads_user_id
                ON {threads_table}(user_id);
            CREATE INDEX IF NOT EXISTS idx_{table_prefix}events_thread_id
                ON {events_table}(thread_id);
            CREATE INDEX IF NOT EXISTS idx_{table_prefix}events_user_id
                ON {events_table}(user_id);
            CREATE INDEX IF NOT EXISTS idx_{table_prefix}runs_thread_id
                ON {runs_table}(thread_id);
            CREATE INDEX IF NOT EXISTS idx_{table_prefix}settings_worker
                ON {settings_table}(worker_id);
        """

    return f"""
        CREATE TABLE IF NOT EXISTS {threads_table} (
            id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            user_id TEXT,
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

        CREATE TABLE IF NOT EXISTS {settings_table} (
            worker_id TEXT PRIMARY KEY,
            values TEXT NOT NULL DEFAULT '{{}}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_{table_prefix}threads_worker_id
            ON {threads_table}(worker_id);
        CREATE INDEX IF NOT EXISTS idx_{table_prefix}threads_user_id
            ON {threads_table}(user_id);
        CREATE INDEX IF NOT EXISTS idx_{table_prefix}events_thread_id
            ON {events_table}(thread_id);
        CREATE INDEX IF NOT EXISTS idx_{table_prefix}events_user_id
            ON {events_table}(user_id);
        CREATE INDEX IF NOT EXISTS idx_{table_prefix}runs_thread_id
            ON {runs_table}(thread_id);
        CREATE INDEX IF NOT EXISTS idx_{table_prefix}settings_worker
            ON {settings_table}(worker_id);
    """
