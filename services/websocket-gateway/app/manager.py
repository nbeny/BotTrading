"""Async connection manager fanning broadcast frames out to WebSocket clients.

A single :class:`ConnectionManager` instance owns the set of connected browser
sockets. The Kafka consumer task calls :meth:`broadcast` for every event; the
manager JSON-encodes the frame once and sends it to every live socket, dropping
any socket that raises (closed/reset) so a dead client never blocks the fan-out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Concurrency-safe registry of active WebSocket connections."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a client. Caller must have awaited ``accept``."""
        async with self._lock:
            self._connections.add(websocket)
        logger.info("client connected total=%d", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a client from the registry (idempotent)."""
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("client disconnected total=%d", len(self._connections))

    @property
    def count(self) -> int:
        return len(self._connections)

    async def broadcast(self, frame: dict[str, Any]) -> None:
        """Send ``frame`` (JSON-encoded) to every connected client.

        Sockets that raise while sending are considered dead and pruned. The
        encode happens once; sends run concurrently so one slow client does not
        serialize the whole fan-out.
        """
        payload = json.dumps(frame, default=str)
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return

        results = await asyncio.gather(
            *(self._send(ws, payload) for ws in targets),
            return_exceptions=True,
        )
        dead = [
            ws
            for ws, res in zip(targets, results, strict=True)
            if isinstance(res, Exception)
        ]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)
            logger.warning("pruned %d dead socket(s)", len(dead))

    @staticmethod
    async def _send(websocket: WebSocket, payload: str) -> None:
        await websocket.send_text(payload)
