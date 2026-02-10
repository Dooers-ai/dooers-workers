import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dooers.features.settings.models import SettingsSchema


@dataclass
class WorkerConfig:
    database_type: Literal["postgres", "sqlite"]

    # Database connection fields (defaults from WORKER_DATABASE_* env vars)
    database_host: str = field(default_factory=lambda: os.environ.get("WORKER_DATABASE_HOST", "localhost"))
    database_port: int = field(default_factory=lambda: int(os.environ.get("WORKER_DATABASE_PORT", "5432")))
    database_user: str = field(default_factory=lambda: os.environ.get("WORKER_DATABASE_USER", "postgres"))
    database_name: str = field(default_factory=lambda: os.environ.get("WORKER_DATABASE_NAME", ""))
    database_password: str = field(default_factory=lambda: os.environ.get("WORKER_DATABASE_PASSWORD", ""))
    database_ssl: bool = field(default_factory=lambda: os.environ.get("WORKER_DATABASE_SSL", "false").lower() == "true")

    table_prefix: str = "worker_"
    auto_migrate: bool = True

    # Analytics (optional, defaults from settings.py)
    analytics_enabled: bool = True
    analytics_webhook_url: str | None = None  # Override default webhook URL
    analytics_batch_size: int | None = None  # Override default
    analytics_flush_interval: float | None = None  # Override default

    # Settings
    settings_schema: "SettingsSchema | None" = None
