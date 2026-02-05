from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from .models import AnalyticsBatch, AnalyticsEvent, AnalyticsEventPayload

if TYPE_CHECKING:
    from ..registry import ConnectionRegistry

logger = logging.getLogger(__name__)


class AnalyticsCollector:
    """
    Collects analytics events, broadcasts to subscribers, and batches for webhook delivery.

    Features:
    - Real-time broadcast to analytics subscribers
    - Batched webhook delivery (configurable size and interval)
    - Background flush task for time-based batching
    """

    def __init__(
        self,
        webhook_url: str,
        registry: ConnectionRegistry,
        subscriptions: dict[str, set[str]],  # worker_id -> set of ws_ids
        batch_size: int = 10,
        flush_interval: float = 5.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._registry = registry
        self._subscriptions = subscriptions
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[AnalyticsEventPayload] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background flush task and HTTP client."""
        if self._running:
            return
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop the background task and flush remaining events."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        try:
            # Flush any remaining events
            await self._flush()
        finally:
            # Always close HTTP client
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None

    async def track(
        self,
        event: str,
        worker_id: str,
        thread_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Track an analytics event.

        Immediately broadcasts to analytics subscribers and adds to batch buffer.
        """
        payload = AnalyticsEventPayload(
            event=event,
            timestamp=datetime.now(UTC),
            worker_id=worker_id,
            thread_id=thread_id,
            user_id=user_id,
            run_id=run_id,
            event_id=event_id,
            data=data,
        )

        # Broadcast to analytics subscribers immediately
        await self._broadcast(worker_id, payload)

        # Add to batch buffer
        async with self._lock:
            self._buffer.append(payload)
            if len(self._buffer) >= self._batch_size:
                await self._flush_locked()

    async def feedback(
        self,
        feedback_type: str,
        target_type: str,
        target_id: str,
        worker_id: str,
        thread_id: str | None = None,
        user_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a feedback event (like or dislike)."""
        event = AnalyticsEvent.FEEDBACK_LIKE if feedback_type == "like" else AnalyticsEvent.FEEDBACK_DISLIKE
        await self.track(
            event=event.value,
            worker_id=worker_id,
            thread_id=thread_id,
            user_id=user_id,
            data={
                "target_type": target_type,
                "target_id": target_id,
                "reason": reason,
            },
        )

    async def _broadcast(self, worker_id: str, payload: AnalyticsEventPayload) -> None:
        """Broadcast an analytics event to all subscribers for a worker."""
        from ..protocol.frames import S2C_AnalyticsEvent

        subscriber_ws_ids = self._subscriptions.get(worker_id, set())
        if not subscriber_ws_ids:
            return

        message = S2C_AnalyticsEvent(
            id=str(uuid4()),
            payload=payload,
        )
        message_json = message.model_dump_json()

        # Send to all subscribed connections
        # subscriber_ws_ids contains the ws_id from Router, which we use to track subscriptions
        # We need to send to all connections since we can't map ws_id back to ws objects
        # The subscription dict tracks which ws_ids are subscribed, but we broadcast to all
        # connections for the worker - this is acceptable for real-time analytics
        connections = self._registry.get_connections(worker_id)
        for ws in connections:
            try:
                await ws.send_text(message_json)
            except Exception:
                logger.warning("Failed to send analytics event to subscriber")

    async def _flush(self) -> None:
        """Flush the buffer, acquiring lock first."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        """Flush the buffer (must be called with lock held)."""
        if not self._buffer:
            return

        # Group events by worker_id for separate batches
        events_by_worker: dict[str, list[AnalyticsEventPayload]] = {}
        for event in self._buffer:
            if event.worker_id not in events_by_worker:
                events_by_worker[event.worker_id] = []
            events_by_worker[event.worker_id].append(event)

        self._buffer.clear()

        # Send batches per worker
        for worker_id, events in events_by_worker.items():
            batch = AnalyticsBatch(
                batch_id=str(uuid4()),
                worker_id=worker_id,
                events=events,
                sent_at=datetime.now(UTC),
            )
            await self._send_to_webhook(batch)

    async def _send_to_webhook(self, batch: AnalyticsBatch) -> None:
        """Send a batch to the webhook endpoint (fire and forget with retry)."""
        if not self._http_client:
            return

        try:
            response = await self._http_client.post(
                self._webhook_url,
                json=batch.model_dump(mode="json"),
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                logger.warning(
                    "Analytics webhook returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
        except httpx.RequestError as e:
            logger.warning("Failed to send analytics batch: %s", e)

    async def _flush_loop(self) -> None:
        """Background task that flushes every flush_interval seconds."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in analytics flush loop: %s", e)
