from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from ..registry import ConnectionRegistry, WebSocketProtocol
    from .models import SettingsSchema

logger = logging.getLogger(__name__)


class SettingsBroadcaster:
    """
    Broadcasts settings changes to subscribed WebSocket connections.

    Supports:
    - Snapshot delivery on subscription
    - Patch broadcast when settings are updated
    """

    def __init__(
        self,
        registry: ConnectionRegistry,
        subscriptions: dict[str, set[str]],  # worker_id -> set of ws_ids
    ) -> None:
        self._registry = registry
        self._subscriptions = subscriptions

    async def broadcast_snapshot_to_ws(
        self,
        worker_id: str,
        ws: WebSocketProtocol,
        schema: SettingsSchema,
        values: dict[str, Any],
    ) -> None:
        """Send a settings snapshot directly to a specific WebSocket connection."""
        from ..protocol.frames import S2C_SettingsSnapshot, SettingsSnapshotPayload
        from .models import SettingsField

        # Merge defaults with stored values
        fields_with_values: list[SettingsField] = []
        for field in schema.fields:
            field_copy = field.model_copy()
            if field.id in values:
                field_copy.value = values[field.id]
            fields_with_values.append(field_copy)

        message = S2C_SettingsSnapshot(
            id=str(uuid4()),
            payload=SettingsSnapshotPayload(
                worker_id=worker_id,
                fields=fields_with_values,
                updated_at=datetime.now(UTC),
            ),
        )

        try:
            await ws.send_text(message.model_dump_json())
        except Exception:
            logger.warning("Failed to send settings snapshot")

    async def broadcast_patch(
        self,
        worker_id: str,
        field_id: str,
        value: Any,
        exclude_ws: WebSocketProtocol | None = None,
    ) -> None:
        """Broadcast a settings patch to all subscribers for a worker."""
        from ..protocol.frames import S2C_SettingsPatch, SettingsPatchBroadcastPayload

        subscriber_ws_ids = self._subscriptions.get(worker_id, set())
        if not subscriber_ws_ids:
            return

        message = S2C_SettingsPatch(
            id=str(uuid4()),
            payload=SettingsPatchBroadcastPayload(
                worker_id=worker_id,
                field_id=field_id,
                value=value,
                updated_at=datetime.now(UTC),
            ),
        )
        message_json = message.model_dump_json()

        # Send to all connections for the worker except the one that made the change
        connections = self._registry.get_connections(worker_id)
        for ws in connections:
            if ws is not exclude_ws:
                try:
                    await ws.send_text(message_json)
                except Exception:
                    logger.warning("Failed to send settings patch to subscriber")
