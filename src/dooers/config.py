from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from dooers.features.settings.models import SettingsSchema


@dataclass
class WorkerConfig:
    database_url: str
    database_type: Literal["postgres", "sqlite"]
    table_prefix: str = "worker_"
    auto_migrate: bool = True

    # Analytics (optional, defaults from settings.py)
    analytics_enabled: bool = True
    analytics_webhook_url: str | None = None  # Override default webhook URL
    analytics_batch_size: int | None = None  # Override default
    analytics_flush_interval: float | None = None  # Override default

    # Settings
    settings_schema: "SettingsSchema | None" = None
