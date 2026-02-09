from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from dooers.features.analytics.models import AnalyticsEvent
from dooers.features.analytics.worker_analytics import WorkerAnalytics
from dooers.features.settings.worker_settings import WorkerSettings
from dooers.handlers.memory import WorkerMemory
from dooers.handlers.request import WorkerRequest
from dooers.handlers.response import WorkerEvent, WorkerResponse
from dooers.persistence.base import Persistence
from dooers.protocol.frames import (
    AckPayload,
    C2S_AnalyticsSubscribe,
    C2S_AnalyticsUnsubscribe,
    C2S_Connect,
    C2S_EventCreate,
    C2S_Feedback,
    C2S_SettingsPatch,
    C2S_SettingsSubscribe,
    C2S_SettingsUnsubscribe,
    C2S_ThreadDelete,
    C2S_ThreadList,
    C2S_ThreadSubscribe,
    C2S_ThreadUnsubscribe,
    ClientToServer,
    EventAppendPayload,
    FeedbackAckPayload,
    RunUpsertPayload,
    S2C_Ack,
    S2C_EventAppend,
    S2C_FeedbackAck,
    S2C_RunUpsert,
    S2C_ThreadDeleted,
    S2C_ThreadListResult,
    S2C_ThreadSnapshot,
    S2C_ThreadUpsert,
    ServerToClient,
    ThreadDeletedPayload,
    ThreadListResultPayload,
    ThreadSnapshotPayload,
    ThreadUpsertPayload,
)
from dooers.protocol.models import DocumentPart, ImagePart, Run, TextPart, Thread, ThreadEvent
from dooers.protocol.parser import serialize_frame
from dooers.registry import ConnectionRegistry

if TYPE_CHECKING:
    from dooers.features.analytics.collector import AnalyticsCollector
    from dooers.features.settings.broadcaster import SettingsBroadcaster
    from dooers.features.settings.models import SettingsSchema

logger = logging.getLogger(__name__)


class WebSocketProtocol(Protocol):
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


Handler = Callable[
    [WorkerRequest, WorkerResponse, WorkerMemory, WorkerAnalytics, WorkerSettings],
    AsyncGenerator[WorkerEvent, None],
]


def _generate_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Router:
    def __init__(
        self,
        persistence: Persistence,
        handler: Handler,
        registry: ConnectionRegistry,
        subscriptions: dict[str, set[str]],
        analytics_collector: AnalyticsCollector | None = None,
        settings_broadcaster: SettingsBroadcaster | None = None,
        settings_schema: SettingsSchema | None = None,
        analytics_subscriptions: dict[str, set[str]] | None = None,
        settings_subscriptions: dict[str, set[str]] | None = None,
    ):
        self._persistence = persistence
        self._handler = handler
        self._registry = registry
        self._subscriptions = subscriptions  # ws_id -> set of subscribed thread_ids

        # Analytics and settings
        self._analytics_collector = analytics_collector
        self._settings_broadcaster = settings_broadcaster
        self._settings_schema = settings_schema
        self._analytics_subscriptions = analytics_subscriptions if analytics_subscriptions is not None else {}
        self._settings_subscriptions = settings_subscriptions if settings_subscriptions is not None else {}

        # Connection state
        self._ws: WebSocketProtocol | None = None
        self._ws_id: str = _generate_id()  # Unique ID for this connection
        self._worker_id: str | None = None
        self._user_id: str | None = None
        self._user_name: str | None = None
        self._user_email: str | None = None
        self._subscribed_threads: set[str] = set()

    async def _send(self, ws: WebSocketProtocol, frame: ServerToClient) -> None:
        await ws.send_text(serialize_frame(frame))

    async def _send_ack(
        self,
        ws: WebSocketProtocol,
        ack_id: str,
        ok: bool = True,
        error: dict | None = None,
    ) -> None:
        frame = S2C_Ack(
            id=_generate_id(),
            payload=AckPayload(ack_id=ack_id, ok=ok, error=error),
        )
        await self._send(ws, frame)

    async def _broadcast_to_worker(self, frame: ServerToClient) -> None:
        """Broadcast a frame to all connections for the current worker."""
        if not self._worker_id:
            return
        message = serialize_frame(frame)
        await self._registry.broadcast(self._worker_id, message)

    async def _broadcast_to_worker_except_self(self, ws: WebSocketProtocol, frame: ServerToClient) -> None:
        """Broadcast a frame to all connections for the current worker except this one."""
        if not self._worker_id:
            return
        message = serialize_frame(frame)
        await self._registry.broadcast_except(self._worker_id, ws, message)

    async def route(self, ws: WebSocketProtocol, frame: ClientToServer) -> None:
        self._ws = ws
        match frame:
            case C2S_Connect():
                await self._handle_connect(ws, frame)
            case C2S_ThreadList():
                await self._handle_thread_list(ws, frame)
            case C2S_ThreadSubscribe():
                await self._handle_thread_subscribe(ws, frame)
            case C2S_ThreadUnsubscribe():
                await self._handle_thread_unsubscribe(ws, frame)
            case C2S_ThreadDelete():
                await self._handle_thread_delete(ws, frame)
            case C2S_EventCreate():
                await self._handle_event_create(ws, frame)
            # Analytics frames
            case C2S_AnalyticsSubscribe():
                await self._handle_analytics_subscribe(ws, frame)
            case C2S_AnalyticsUnsubscribe():
                await self._handle_analytics_unsubscribe(ws, frame)
            case C2S_Feedback():
                await self._handle_feedback(ws, frame)
            # Settings frames
            case C2S_SettingsSubscribe():
                await self._handle_settings_subscribe(ws, frame)
            case C2S_SettingsUnsubscribe():
                await self._handle_settings_unsubscribe(ws, frame)
            case C2S_SettingsPatch():
                await self._handle_settings_patch(ws, frame)

    async def cleanup(self) -> None:
        """Clean up connection resources. Call this when the connection closes."""
        logger.info(
            "Router cleanup: ws=%s, worker=%s, had_analytics_sub=%s",
            self._ws_id,
            self._worker_id,
            self._ws_id in self._analytics_subscriptions.get(self._worker_id or "", set()),
        )
        if self._worker_id:
            await self._registry.unregister(self._worker_id, self._ws)

            # Clean up analytics subscriptions
            if self._worker_id in self._analytics_subscriptions:
                self._analytics_subscriptions[self._worker_id].discard(self._ws_id)
                if not self._analytics_subscriptions[self._worker_id]:
                    logger.info("Analytics subscriptions emptied for worker %s — deleting key", self._worker_id)
                    del self._analytics_subscriptions[self._worker_id]

            # Clean up settings subscriptions
            if self._worker_id in self._settings_subscriptions:
                self._settings_subscriptions[self._worker_id].discard(self._ws_id)
                if not self._settings_subscriptions[self._worker_id]:
                    del self._settings_subscriptions[self._worker_id]

        # Clean up thread subscriptions tracking
        if self._ws_id in self._subscriptions:
            del self._subscriptions[self._ws_id]

    async def _handle_connect(self, ws: WebSocketProtocol, frame: C2S_Connect) -> None:
        # Extract identity from payload
        self._worker_id = frame.payload.worker_id
        self._user_id = frame.payload.user_id
        self._user_name = frame.payload.user_name
        self._user_email = frame.payload.user_email
        self._ws = ws

        # Register connection in registry
        await self._registry.register(self._worker_id, ws)

        # Initialize subscriptions for this connection
        self._subscriptions[self._ws_id] = set()

        await self._send_ack(ws, frame.id)

    async def _handle_thread_list(self, ws: WebSocketProtocol, frame: C2S_ThreadList) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        # Filter by worker_id first, then optionally by user_id
        threads = await self._persistence.list_threads(
            worker_id=self._worker_id,
            user_id=None,  # Show all threads for this worker (team collaboration)
            cursor=frame.payload.cursor,
            limit=frame.payload.limit or 30,
        )
        result = S2C_ThreadListResult(
            id=_generate_id(),
            payload=ThreadListResultPayload(threads=threads, cursor=None),
        )
        await self._send(ws, result)

    async def _handle_thread_subscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_ThreadSubscribe,
    ) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        thread_id = frame.payload.thread_id
        thread = await self._persistence.get_thread(thread_id)

        if not thread:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_FOUND", "message": "Thread not found"},
            )
            return

        # Verify thread belongs to this worker
        if thread.worker_id != self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "FORBIDDEN", "message": "Thread belongs to different worker"},
            )
            return

        events = await self._persistence.get_events(
            thread_id,
            after_event_id=frame.payload.after_event_id,
            limit=100,
        )

        self._subscribed_threads.add(thread_id)
        self._subscriptions[self._ws_id].add(thread_id)

        snapshot = S2C_ThreadSnapshot(
            id=_generate_id(),
            payload=ThreadSnapshotPayload(thread=thread, events=events),
        )
        await self._send(ws, snapshot)

    async def _handle_thread_unsubscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_ThreadUnsubscribe,
    ) -> None:
        thread_id = frame.payload.thread_id
        self._subscribed_threads.discard(thread_id)
        if self._ws_id in self._subscriptions:
            self._subscriptions[self._ws_id].discard(thread_id)
        await self._send_ack(ws, frame.id)

    async def _handle_thread_delete(self, ws: WebSocketProtocol, frame: C2S_ThreadDelete) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        thread_id = frame.payload.thread_id
        thread = await self._persistence.get_thread(thread_id)

        if not thread:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_FOUND", "message": "Thread not found"},
            )
            return

        if thread.worker_id != self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "FORBIDDEN", "message": "Thread belongs to different worker"},
            )
            return

        await self._persistence.delete_thread(thread_id)

        # Remove from subscriptions
        self._subscribed_threads.discard(thread_id)
        if self._ws_id in self._subscriptions:
            self._subscriptions[self._ws_id].discard(thread_id)

        # Broadcast deletion to all worker connections
        deleted_frame = S2C_ThreadDeleted(
            id=_generate_id(),
            payload=ThreadDeletedPayload(thread_id=thread_id),
        )
        await self._broadcast_to_worker(deleted_frame)

        await self._send_ack(ws, frame.id)

    async def _handle_event_create(self, ws: WebSocketProtocol, frame: C2S_EventCreate) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        now = _now()
        thread_id = frame.payload.thread_id

        if not thread_id:
            # Create new thread for this worker
            thread_id = _generate_id()
            thread = Thread(
                id=thread_id,
                worker_id=self._worker_id,
                user_id=self._user_id,
                title=None,
                created_at=now,
                updated_at=now,
                last_event_at=now,
            )
            await self._persistence.create_thread(thread)
            self._subscribed_threads.add(thread_id)
            self._subscriptions[self._ws_id].add(thread_id)

            # Broadcast new thread to ALL worker connections
            thread_upsert = S2C_ThreadUpsert(
                id=_generate_id(),
                payload=ThreadUpsertPayload(thread=thread),
            )
            await self._broadcast_to_worker(thread_upsert)

            # Track thread.created
            await self._track_event(
                AnalyticsEvent.THREAD_CREATED.value,
                thread_id=thread_id,
            )
        else:
            # Verify thread belongs to this worker
            thread = await self._persistence.get_thread(thread_id)
            if not thread:
                await self._send_ack(
                    ws,
                    frame.id,
                    ok=False,
                    error={"code": "NOT_FOUND", "message": "Thread not found"},
                )
                return
            if thread.worker_id != self._worker_id:
                await self._send_ack(
                    ws,
                    frame.id,
                    ok=False,
                    error={"code": "FORBIDDEN", "message": "Thread belongs to different worker"},
                )
                return

        # Create user event with identity
        user_event_id = _generate_id()
        content_parts = [self._convert_content_part(part) for part in frame.payload.event.content]
        user_event = ThreadEvent(
            id=user_event_id,
            thread_id=thread_id,
            run_id=None,
            type="message",
            actor="user",
            user_id=self._user_id,
            user_name=self._user_name,
            user_email=self._user_email,
            content=content_parts,
            data=frame.payload.event.data,
            created_at=now,
        )
        await self._persistence.create_event(user_event)

        # Broadcast user event to ALL worker connections
        event_append = S2C_EventAppend(
            id=_generate_id(),
            payload=EventAppendPayload(thread_id=thread_id, events=[user_event]),
        )
        await self._broadcast_to_worker(event_append)

        # Track message.c2s
        await self._track_event(
            AnalyticsEvent.MESSAGE_C2S.value,
            thread_id=thread_id,
            event_id=user_event_id,
        )

        await self._send_ack(ws, frame.id)

        # Process with handler
        message = self._extract_message(content_parts)
        request = WorkerRequest(
            message=message,
            content=content_parts,
            thread_id=thread_id,
            event_id=user_event_id,
            user_id=self._user_id,
            user_name=self._user_name,
            user_email=self._user_email,
        )
        response = WorkerResponse()
        memory = WorkerMemory(thread_id=thread_id, persistence=self._persistence)

        # Create analytics instance for handler
        analytics = (
            WorkerAnalytics(
                worker_id=self._worker_id,
                thread_id=thread_id,
                user_id=self._user_id,
                run_id=None,  # Will be set when run starts
                collector=self._analytics_collector,
            )
            if self._analytics_collector
            else self._create_noop_analytics()
        )

        # Create settings instance for handler
        settings = (
            WorkerSettings(
                worker_id=self._worker_id,
                schema=self._settings_schema,
                persistence=self._persistence,
                broadcaster=self._settings_broadcaster,
            )
            if self._settings_schema and self._settings_broadcaster
            else self._create_noop_settings()
        )

        current_run_id: str | None = None

        try:
            async for event in self._handler(request, response, memory, analytics, settings):
                event_now = _now()

                if event.response_type == "run_start":
                    current_run_id = _generate_id()
                    run = Run(
                        id=current_run_id,
                        thread_id=thread_id,
                        agent_id=event.data.get("agent_id"),
                        status="running",
                        started_at=event_now,
                    )
                    await self._persistence.create_run(run)

                    run_upsert = S2C_RunUpsert(
                        id=_generate_id(),
                        payload=RunUpsertPayload(run=run),
                    )
                    await self._broadcast_to_worker(run_upsert)

                elif event.response_type == "run_end":
                    if current_run_id:
                        run = Run(
                            id=current_run_id,
                            thread_id=thread_id,
                            status=event.data.get("status", "succeeded"),
                            started_at=event_now,
                            ended_at=event_now,
                            error=event.data.get("error"),
                        )
                        await self._persistence.update_run(run)

                        run_upsert = S2C_RunUpsert(
                            id=_generate_id(),
                            payload=RunUpsertPayload(run=run),
                        )
                        await self._broadcast_to_worker(run_upsert)
                        current_run_id = None

                elif event.response_type == "text":
                    event_id = _generate_id()
                    thread_event = ThreadEvent(
                        id=event_id,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        type="message",
                        actor="assistant",
                        user_id=None,
                        user_name=None,
                        user_email=None,
                        content=[TextPart(text=event.data["text"])],
                        created_at=event_now,
                    )
                    await self._persistence.create_event(thread_event)

                    append = S2C_EventAppend(
                        id=_generate_id(),
                        payload=EventAppendPayload(thread_id=thread_id, events=[thread_event]),
                    )
                    await self._broadcast_to_worker(append)

                    # Track message.s2c
                    await self._track_event(
                        AnalyticsEvent.MESSAGE_S2C.value,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        event_id=event_id,
                        data={"type": "text"},
                    )

                elif event.response_type == "image":
                    event_id = _generate_id()
                    thread_event = ThreadEvent(
                        id=event_id,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        type="message",
                        actor="assistant",
                        user_id=None,
                        user_name=None,
                        user_email=None,
                        content=[
                            ImagePart(
                                url=event.data["url"],
                                mime_type=event.data.get("mime_type"),
                                alt=event.data.get("alt"),
                            )
                        ],
                        created_at=event_now,
                    )
                    await self._persistence.create_event(thread_event)

                    append = S2C_EventAppend(
                        id=_generate_id(),
                        payload=EventAppendPayload(thread_id=thread_id, events=[thread_event]),
                    )
                    await self._broadcast_to_worker(append)

                    # Track message.s2c
                    await self._track_event(
                        AnalyticsEvent.MESSAGE_S2C.value,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        event_id=event_id,
                        data={"type": "image"},
                    )

                elif event.response_type == "document":
                    event_id = _generate_id()
                    thread_event = ThreadEvent(
                        id=event_id,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        type="message",
                        actor="assistant",
                        user_id=None,
                        user_name=None,
                        user_email=None,
                        content=[
                            DocumentPart(
                                url=event.data["url"],
                                filename=event.data["filename"],
                                mime_type=event.data["mime_type"],
                            )
                        ],
                        created_at=event_now,
                    )
                    await self._persistence.create_event(thread_event)

                    append = S2C_EventAppend(
                        id=_generate_id(),
                        payload=EventAppendPayload(thread_id=thread_id, events=[thread_event]),
                    )
                    await self._broadcast_to_worker(append)

                    # Track message.s2c
                    await self._track_event(
                        AnalyticsEvent.MESSAGE_S2C.value,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        event_id=event_id,
                        data={"type": "document"},
                    )

                elif event.response_type == "tool_call":
                    event_id = _generate_id()
                    thread_event = ThreadEvent(
                        id=event_id,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        type="tool.call",
                        actor="assistant",
                        user_id=None,
                        user_name=None,
                        user_email=None,
                        data={
                            "name": event.data["name"],
                            "args": event.data["args"],
                        },
                        created_at=event_now,
                    )
                    await self._persistence.create_event(thread_event)

                    append = S2C_EventAppend(
                        id=_generate_id(),
                        payload=EventAppendPayload(thread_id=thread_id, events=[thread_event]),
                    )
                    await self._broadcast_to_worker(append)

                    # Track tool.called
                    await self._track_event(
                        AnalyticsEvent.TOOL_CALLED.value,
                        thread_id=thread_id,
                        run_id=current_run_id,
                        event_id=event_id,
                        data={"name": event.data["name"]},
                    )

                elif event.response_type == "tool_result":
                    thread_event = ThreadEvent(
                        id=_generate_id(),
                        thread_id=thread_id,
                        run_id=current_run_id,
                        type="tool.result",
                        actor="tool",
                        user_id=None,
                        user_name=None,
                        user_email=None,
                        data={
                            "name": event.data["name"],
                            "result": event.data["result"],
                        },
                        created_at=event_now,
                    )
                    await self._persistence.create_event(thread_event)

                    append = S2C_EventAppend(
                        id=_generate_id(),
                        payload=EventAppendPayload(thread_id=thread_id, events=[thread_event]),
                    )
                    await self._broadcast_to_worker(append)

                await self._update_thread_last_event(thread_id, event_now)

        except Exception as e:
            # Track error.occurred
            await self._track_event(
                AnalyticsEvent.ERROR_OCCURRED.value,
                thread_id=thread_id,
                run_id=current_run_id,
                data={"error": str(e), "type": type(e).__name__},
            )

            if current_run_id:
                error_now = _now()
                run = Run(
                    id=current_run_id,
                    thread_id=thread_id,
                    status="failed",
                    started_at=error_now,
                    ended_at=error_now,
                    error=str(e),
                )
                await self._persistence.update_run(run)

                run_upsert = S2C_RunUpsert(
                    id=_generate_id(),
                    payload=RunUpsertPayload(run=run),
                )
                await self._broadcast_to_worker(run_upsert)

    async def _update_thread_last_event(self, thread_id: str, timestamp: datetime) -> None:
        thread = await self._persistence.get_thread(thread_id)
        if thread:
            thread.last_event_at = timestamp
            thread.updated_at = timestamp
            await self._persistence.update_thread(thread)

    def _convert_content_part(self, part):
        if hasattr(part, "model_dump"):
            data = part.model_dump()
        else:
            data = dict(part) if hasattr(part, "__iter__") else part

        part_type = data.get("type")
        if part_type == "text":
            return TextPart(**data)
        elif part_type == "image":
            return ImagePart(**data)
        elif part_type == "document":
            return DocumentPart(**data)
        return part

    def _extract_message(self, content: list) -> str:
        texts = []
        for part in content:
            if isinstance(part, TextPart):
                texts.append(part.text)
        return " ".join(texts)

    # Analytics frame handlers

    async def _handle_analytics_subscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_AnalyticsSubscribe,
    ) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        worker_id = frame.payload.worker_id
        if worker_id != self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "FORBIDDEN", "message": "Cannot subscribe to other worker's analytics"},
            )
            return

        # Add to analytics subscriptions
        if worker_id not in self._analytics_subscriptions:
            self._analytics_subscriptions[worker_id] = set()
        self._analytics_subscriptions[worker_id].add(self._ws_id)

        logger.info(
            "Analytics subscribed: ws=%s, worker=%s, total_subscribers=%d",
            self._ws_id,
            worker_id,
            len(self._analytics_subscriptions[worker_id]),
        )

        await self._send_ack(ws, frame.id)

    async def _handle_analytics_unsubscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_AnalyticsUnsubscribe,
    ) -> None:
        worker_id = frame.payload.worker_id
        logger.info(
            "Analytics unsubscribed: ws=%s, worker=%s",
            self._ws_id,
            worker_id,
        )
        if worker_id in self._analytics_subscriptions:
            self._analytics_subscriptions[worker_id].discard(self._ws_id)
            if not self._analytics_subscriptions[worker_id]:
                del self._analytics_subscriptions[worker_id]

        await self._send_ack(ws, frame.id)

    async def _handle_feedback(
        self,
        ws: WebSocketProtocol,
        frame: C2S_Feedback,
    ) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        if self._analytics_collector:
            await self._analytics_collector.feedback(
                feedback_type=frame.payload.feedback,
                target_type=frame.payload.target_type,
                target_id=frame.payload.target_id,
                worker_id=self._worker_id,
                user_id=self._user_id,
                reason=frame.payload.reason,
            )

        # Send feedback acknowledgment
        ack = S2C_FeedbackAck(
            id=_generate_id(),
            payload=FeedbackAckPayload(
                target_type=frame.payload.target_type,
                target_id=frame.payload.target_id,
                feedback=frame.payload.feedback,
                ok=True,
            ),
        )
        await self._send(ws, ack)

    # Settings frame handlers

    async def _handle_settings_subscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_SettingsSubscribe,
    ) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        worker_id = frame.payload.worker_id
        if worker_id != self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "FORBIDDEN", "message": "Cannot subscribe to other worker's settings"},
            )
            return

        if not self._settings_schema:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONFIGURED", "message": "No settings schema configured"},
            )
            return

        # Add to settings subscriptions
        if worker_id not in self._settings_subscriptions:
            self._settings_subscriptions[worker_id] = set()
        self._settings_subscriptions[worker_id].add(self._ws_id)

        # Send settings snapshot directly to this connection
        if self._settings_broadcaster:
            values = await self._persistence.get_settings(worker_id)
            await self._settings_broadcaster.broadcast_snapshot_to_ws(
                worker_id=worker_id,
                ws=ws,
                schema=self._settings_schema,
                values=values,
            )

        await self._send_ack(ws, frame.id)

    async def _handle_settings_unsubscribe(
        self,
        ws: WebSocketProtocol,
        frame: C2S_SettingsUnsubscribe,
    ) -> None:
        worker_id = frame.payload.worker_id
        if worker_id in self._settings_subscriptions:
            self._settings_subscriptions[worker_id].discard(self._ws_id)
            if not self._settings_subscriptions[worker_id]:
                del self._settings_subscriptions[worker_id]

        await self._send_ack(ws, frame.id)

    async def _handle_settings_patch(
        self,
        ws: WebSocketProtocol,
        frame: C2S_SettingsPatch,
    ) -> None:
        if not self._worker_id:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "Must connect first"},
            )
            return

        if not self._settings_schema:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_CONFIGURED", "message": "No settings schema configured"},
            )
            return

        field_id = frame.payload.field_id
        value = frame.payload.value

        # Validate field exists and is not readonly
        field = self._settings_schema.get_field(field_id)
        if not field:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "NOT_FOUND", "message": f"Unknown field: {field_id}"},
            )
            return

        if field.readonly:
            await self._send_ack(
                ws,
                frame.id,
                ok=False,
                error={"code": "READONLY", "message": f"Field '{field_id}' is readonly"},
            )
            return

        # Update setting
        await self._persistence.update_setting(self._worker_id, field_id, value)

        # Broadcast to other subscribers (exclude sender)
        if self._settings_broadcaster:
            await self._settings_broadcaster.broadcast_patch(
                worker_id=self._worker_id,
                field_id=field_id,
                value=value,
                exclude_ws=ws,
            )

        await self._send_ack(ws, frame.id)

    # Analytics tracking helpers

    async def _track_event(
        self,
        event: str,
        thread_id: str | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        """Track an analytics event if collector is enabled."""
        if self._analytics_collector and self._worker_id:
            await self._analytics_collector.track(
                event=event,
                worker_id=self._worker_id,
                thread_id=thread_id,
                user_id=self._user_id,
                run_id=run_id,
                event_id=event_id,
                data=data,
            )

    def _create_noop_analytics(self) -> WorkerAnalytics:
        """Create a no-op analytics instance when collector is disabled."""

        # Create a minimal collector that does nothing
        class NoopCollector:
            async def track(self, **kwargs) -> None:
                pass

            async def feedback(self, **kwargs) -> None:
                pass

        return WorkerAnalytics(
            worker_id=self._worker_id or "",
            thread_id="",
            user_id=None,
            run_id=None,
            collector=NoopCollector(),  # type: ignore
        )

    def _create_noop_settings(self) -> WorkerSettings:
        """Create a no-op settings instance when settings are not configured."""
        from dooers.features.settings.models import SettingsSchema

        class NoopBroadcaster:
            async def broadcast_snapshot(self, **kwargs) -> None:
                pass

            async def broadcast_patch(self, **kwargs) -> None:
                pass

        class NoopPersistence:
            async def get_settings(self, worker_id: str) -> dict:
                return {}

            async def update_setting(self, worker_id: str, field_id: str, value) -> None:
                pass

            async def set_settings(self, worker_id: str, values: dict) -> None:
                pass

        return WorkerSettings(
            worker_id=self._worker_id or "",
            schema=SettingsSchema(fields=[]),
            persistence=NoopPersistence(),  # type: ignore
            broadcaster=NoopBroadcaster(),  # type: ignore
        )
