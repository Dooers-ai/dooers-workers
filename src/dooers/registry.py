from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    pass


class WebSocketProtocol(Protocol):
    """Protocol for WebSocket connections."""

    async def send_text(self, data: str) -> None: ...
    async def receive_text(self) -> str: ...


class ConnectionRegistry:
    """
    Tracks active WebSocket connections by worker_id.

    Enables broadcasting messages to all connections for a specific worker,
    supporting real-time sync across multiple users/tabs.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocketProtocol]] = {}
        self._lock = asyncio.Lock()

    async def register(self, worker_id: str, ws: WebSocketProtocol) -> None:
        """Register a WebSocket connection for a worker."""
        async with self._lock:
            if worker_id not in self._connections:
                self._connections[worker_id] = set()
            self._connections[worker_id].add(ws)

    async def unregister(self, worker_id: str, ws: WebSocketProtocol) -> None:
        """Unregister a WebSocket connection for a worker."""
        async with self._lock:
            if worker_id in self._connections:
                self._connections[worker_id].discard(ws)
                if not self._connections[worker_id]:
                    del self._connections[worker_id]

    def get_connections(self, worker_id: str) -> set[WebSocketProtocol]:
        """Get all connections for a worker (returns copy to avoid mutation)."""
        return self._connections.get(worker_id, set()).copy()

    def get_connection_count(self, worker_id: str) -> int:
        """Get the number of active connections for a worker."""
        return len(self._connections.get(worker_id, set()))

    async def broadcast(
        self,
        worker_id: str,
        message: str,
    ) -> int:
        """
        Send a message to ALL connections for a worker_id.

        Returns the number of connections that received the message.
        """
        connections = self.get_connections(worker_id)
        if not connections:
            return 0

        results = await asyncio.gather(
            *[self._safe_send(ws, message) for ws in connections],
            return_exceptions=True,
        )
        return sum(1 for r in results if r is True)

    async def broadcast_except(
        self,
        worker_id: str,
        exclude: WebSocketProtocol,
        message: str,
    ) -> int:
        """
        Send a message to all connections for a worker_id except one.

        Useful for broadcasting events without sending back to the originator.
        Returns the number of connections that received the message.
        """
        connections = self.get_connections(worker_id)
        connections.discard(exclude)
        if not connections:
            return 0

        results = await asyncio.gather(
            *[self._safe_send(ws, message) for ws in connections],
            return_exceptions=True,
        )
        return sum(1 for r in results if r is True)

    async def _safe_send(self, ws: WebSocketProtocol, message: str) -> bool:
        """Send a message to a WebSocket, catching any errors."""
        try:
            await ws.send_text(message)
            return True
        except Exception:
            return False
