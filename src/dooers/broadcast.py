from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dooers.protocol.frames import (
    EventAppendPayload,
    S2C_EventAppend,
    S2C_ThreadUpsert,
    ThreadUpsertPayload,
)
from dooers.protocol.models import Actor, ContentPart, Thread, ThreadEvent
from dooers.protocol.parser import serialize_frame

if TYPE_CHECKING:
    from dooers.persistence.base import Persistence
    from dooers.registry import ConnectionRegistry


def _generate_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class BroadcastManager:
    """
    Manages broadcasting events from backend to connected WebSocket clients.

    Use this to push messages, notifications, or events from external systems
    (webhooks, background jobs, etc.) to all connected users of a worker.
    """

    def __init__(
        self,
        registry: ConnectionRegistry,
        persistence: Persistence,
        subscriptions: dict[str, set[str]],  # worker_id -> set of subscribed thread_ids per connection
    ) -> None:
        self._registry = registry
        self._persistence = persistence
        self._subscriptions = subscriptions  # Shared with Router instances

    async def send_event(
        self,
        worker_id: str,
        thread_id: str,
        content: list[ContentPart],
        actor: Actor = "system",
        user_id: str | None = None,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> tuple[ThreadEvent, int]:
        """
        Create and broadcast an event from the backend to all connected clients.

        Args:
            worker_id: The worker instance to broadcast to
            thread_id: The thread to add the event to
            content: Message content (text, images, documents)
            actor: Event actor (default: "system")
            user_id: Optional user identifier
            user_name: Optional display name
            user_email: Optional email

        Returns:
            Tuple of (created event, number of connections notified)

        Raises:
            ValueError: If thread not found
            PermissionError: If thread belongs to different worker
        """
        # Verify thread exists and belongs to worker
        thread = await self._persistence.get_thread(thread_id)
        if not thread:
            raise ValueError(f"Thread {thread_id} not found")
        if thread.worker_id != worker_id:
            raise PermissionError(f"Thread {thread_id} belongs to different worker")

        now = _now()

        # Create the event
        event = ThreadEvent(
            id=_generate_id(),
            thread_id=thread_id,
            run_id=None,
            type="message",
            actor=actor,
            user_id=user_id,
            user_name=user_name,
            user_email=user_email,
            content=content,
            data=None,
            created_at=now,
        )

        # Persist
        await self._persistence.create_event(event)

        # Update thread timestamp
        thread.last_event_at = now
        thread.updated_at = now
        await self._persistence.update_thread(thread)

        # Broadcast to all connections for this worker
        frame = S2C_EventAppend(
            id=_generate_id(),
            payload=EventAppendPayload(thread_id=thread_id, events=[event]),
        )
        message = serialize_frame(frame)
        count = await self._registry.broadcast(worker_id, message)

        return event, count

    async def send_thread_update(
        self,
        worker_id: str,
        thread: Thread,
    ) -> int:
        """
        Broadcast a thread update to all connected clients.

        Args:
            worker_id: The worker instance to broadcast to
            thread: The thread that was updated

        Returns:
            Number of connections notified
        """
        if thread.worker_id != worker_id:
            raise PermissionError(f"Thread {thread.id} belongs to different worker")

        frame = S2C_ThreadUpsert(
            id=_generate_id(),
            payload=ThreadUpsertPayload(thread=thread),
        )
        message = serialize_frame(frame)
        return await self._registry.broadcast(worker_id, message)

    async def create_thread_and_broadcast(
        self,
        worker_id: str,
        organization_id: str,
        workspace_id: str,
        user_id: str,
        title: str | None = None,
    ) -> tuple[Thread, int]:
        """
        Create a new thread and broadcast it to all connected clients.

        Args:
            worker_id: The worker instance
            organization_id: Organization identifier
            workspace_id: Workspace identifier
            user_id: User who owns the thread
            title: Optional thread title

        Returns:
            Tuple of (created thread, number of connections notified)
        """
        now = _now()
        thread = Thread(
            id=_generate_id(),
            worker_id=worker_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
            last_event_at=now,
        )

        await self._persistence.create_thread(thread)

        frame = S2C_ThreadUpsert(
            id=_generate_id(),
            payload=ThreadUpsertPayload(thread=thread),
        )
        message = serialize_frame(frame)
        count = await self._registry.broadcast(worker_id, message)

        return thread, count
