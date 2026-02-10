import logging

from dooers.broadcast import BroadcastManager
from dooers.config import WorkerConfig
from dooers.features.analytics.collector import AnalyticsCollector
from dooers.features.settings.broadcaster import SettingsBroadcaster
from dooers.handlers.router import Handler, Router, WebSocketProtocol
from dooers.persistence.base import Persistence
from dooers.persistence.postgres import PostgresPersistence
from dooers.persistence.sqlite import SqlitePersistence
from dooers.protocol.parser import parse_frame
from dooers.registry import ConnectionRegistry
from dooers.settings import (
    ANALYTICS_BATCH_SIZE,
    ANALYTICS_FLUSH_INTERVAL,
    ANALYTICS_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)


class WorkerServer:
    def __init__(self, config: WorkerConfig):
        self._config = config
        self._persistence: Persistence | None = None
        self._initialized = False

        # Shared state across all connections
        self._registry = ConnectionRegistry()
        self._subscriptions: dict[str, set[str]] = {}  # ws_id -> set of thread_ids

        # Analytics and settings subscriptions
        self._analytics_subscriptions: dict[str, set[str]] = {}  # worker_id -> set of ws_ids
        self._settings_subscriptions: dict[str, set[str]] = {}  # worker_id -> set of ws_ids

        # Broadcast manager (initialized after persistence)
        self._broadcast: BroadcastManager | None = None

        # Analytics collector (initialized after persistence)
        self._analytics_collector: AnalyticsCollector | None = None

        # Settings broadcaster (initialized after persistence)
        self._settings_broadcaster: SettingsBroadcaster | None = None

    @property
    def registry(self) -> ConnectionRegistry:
        """Access the connection registry for broadcasting."""
        return self._registry

    @property
    def persistence(self) -> Persistence:
        """Access the persistence layer for external thread/event management."""
        if not self._persistence:
            raise RuntimeError("Server not initialized. Call handle() first or ensure_initialized().")
        return self._persistence

    @property
    def broadcast(self) -> BroadcastManager:
        """Access the broadcast manager for sending events from backend."""
        if not self._broadcast:
            raise RuntimeError("Server not initialized. Call handle() first or ensure_initialized().")
        return self._broadcast

    async def _ensure_initialized(self) -> Persistence:
        if self._persistence and self._initialized:
            return self._persistence

        if self._config.database_type == "sqlite":
            self._persistence = SqlitePersistence(
                database_name=self._config.database_name,
                table_prefix=self._config.table_prefix,
            )
        else:
            self._persistence = PostgresPersistence(
                host=self._config.database_host,
                port=self._config.database_port,
                user=self._config.database_user,
                database=self._config.database_name,
                password=self._config.database_password,
                ssl=self._config.database_ssl,
                table_prefix=self._config.table_prefix,
            )

        await self._persistence.connect()

        if self._config.auto_migrate:
            await self._persistence.migrate()

        # Initialize broadcast manager
        self._broadcast = BroadcastManager(
            registry=self._registry,
            persistence=self._persistence,
            subscriptions=self._subscriptions,
        )

        # Initialize analytics collector if enabled
        if self._config.analytics_enabled:
            batch_size = self._config.analytics_batch_size or ANALYTICS_BATCH_SIZE
            flush_interval = self._config.analytics_flush_interval or ANALYTICS_FLUSH_INTERVAL

            webhook_url = self._config.analytics_webhook_url or ANALYTICS_WEBHOOK_URL
            self._analytics_collector = AnalyticsCollector(
                webhook_url=webhook_url,
                registry=self._registry,
                subscriptions=self._analytics_subscriptions,
                batch_size=batch_size,
                flush_interval=flush_interval,
            )
            await self._analytics_collector.start()

        # Initialize settings broadcaster
        self._settings_broadcaster = SettingsBroadcaster(
            registry=self._registry,
            subscriptions=self._settings_subscriptions,
        )

        self._initialized = True
        return self._persistence

    async def ensure_initialized(self) -> None:
        """Explicitly initialize the server (useful for accessing broadcast before handling connections)."""
        await self._ensure_initialized()

    async def migrate(self) -> None:
        persistence = await self._ensure_initialized()
        await persistence.migrate()

    async def handle(self, websocket: WebSocketProtocol, handler: Handler) -> None:
        persistence = await self._ensure_initialized()
        router = Router(
            persistence=persistence,
            handler=handler,
            registry=self._registry,
            subscriptions=self._subscriptions,
            analytics_collector=self._analytics_collector,
            settings_broadcaster=self._settings_broadcaster,
            settings_schema=self._config.settings_schema,
            analytics_subscriptions=self._analytics_subscriptions,
            settings_subscriptions=self._settings_subscriptions,
        )

        try:
            while True:
                data = await websocket.receive_text()
                frame = parse_frame(data)
                await router.route(websocket, frame)
        except Exception as e:
            # Log unexpected errors (connection closed errors are expected)
            error_name = type(e).__name__
            if error_name not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError"):
                logger.debug("WebSocket connection error: %s: %s", error_name, e)
        finally:
            # Clean up connection resources
            await router.cleanup()

    async def close(self) -> None:
        # Stop analytics collector
        if self._analytics_collector:
            await self._analytics_collector.stop()
            self._analytics_collector = None

        # Disconnect persistence
        if self._persistence:
            await self._persistence.disconnect()
            self._persistence = None

        self._initialized = False
