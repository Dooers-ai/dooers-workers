from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..persistence.base import Persistence
    from .broadcaster import SettingsBroadcaster
    from .models import SettingsSchema


class WorkerSettings:
    """
    Handler API for dynamic settings.

    Provides methods for getting and setting worker-level configuration.
    Passed as the fifth parameter to handler functions.
    """

    def __init__(
        self,
        worker_id: str,
        schema: SettingsSchema,
        persistence: Persistence,
        broadcaster: SettingsBroadcaster,
    ) -> None:
        self._worker_id = worker_id
        self._schema = schema
        self._persistence = persistence
        self._broadcaster = broadcaster

    async def get(self, field_id: str) -> Any:
        """
        Get a single field value.

        Args:
            field_id: The field ID to get

        Returns:
            The field value (stored or default)

        Raises:
            KeyError: If field_id doesn't exist in schema
        """
        field = self._schema.get_field(field_id)
        if not field:
            raise KeyError(f"Unknown field: {field_id}")

        values = await self._persistence.get_settings(self._worker_id)
        return values.get(field_id, field.value)

    async def get_all(self) -> dict[str, Any]:
        """
        Get all field values as a dict.

        Returns:
            Dict mapping field_id to value (stored values merged with defaults)
        """
        defaults = self._schema.get_defaults()
        stored = await self._persistence.get_settings(self._worker_id)
        return {**defaults, **stored}

    async def set(self, field_id: str, value: Any) -> None:
        """
        Update a field value and broadcast to subscribers.

        Args:
            field_id: The field ID to update
            value: The new value

        Raises:
            KeyError: If field_id doesn't exist in schema
            ValueError: If field is readonly
        """
        self._validate_field(field_id, value)
        await self._persistence.update_setting(self._worker_id, field_id, value)
        await self._broadcaster.broadcast_patch(self._worker_id, field_id, value)

    def _validate_field(self, field_id: str, value: Any) -> None:
        """Validate that a field exists and can be set."""
        field = self._schema.get_field(field_id)
        if not field:
            raise KeyError(f"Unknown field: {field_id}")
        if field.readonly:
            raise ValueError(f"Field '{field_id}' is readonly")
        # Additional type validation can be added here in the future
