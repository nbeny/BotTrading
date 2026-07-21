"""websocket-gateway entrypoint: bridges Kafka events to browser WebSockets.

Flow: Kafka topics -> :class:`BroadcastConsumer` -> :class:`ConnectionManager`
-> connected terminal clients. The consumer task is started/stopped in the
FastAPI lifespan, mirroring every other CMI service.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, status

from cmi_common import Settings, create_app

from .auth import InvalidTokenError, decode_token
from .deps import Deps, get_deps

logger = logging.getLogger(__name__)


async def _startup(app: FastAPI, settings: Settings) -> None:
    deps = Deps.build(settings)
    await deps.consumer.start()
    app.state.deps = deps
    app.state.consumer_task = asyncio.create_task(deps.consumer.run())


async def _shutdown(app: FastAPI, settings: Settings) -> None:
    deps: Deps = app.state.deps
    await deps.consumer.stop()
    await asyncio.gather(app.state.consumer_task, return_exceptions=True)


app = create_app("websocket-gateway", on_startup=_startup, on_shutdown=_shutdown)


@app.websocket("/ws")
async def ws(
    websocket: WebSocket,
    token: str | None = None,
    deps: Deps = Depends(get_deps),
) -> None:
    """Operator terminal stream.

    Optional ``?token=<jwt>`` auth hook: verified against ``JWT_SECRET`` when
    configured, otherwise decoded best-effort in dev mode. Once accepted the
    client is registered with the manager and receives every broadcast frame
    until it disconnects.
    """
    try:
        principal = decode_token(token)
    except InvalidTokenError as exc:
        logger.warning("ws auth rejected: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await deps.manager.connect(websocket)
    logger.info("ws stream open sub=%s role=%s", principal.sub, principal.role)
    try:
        # Frames are pushed by the broadcast consumer; we only read to detect
        # client-side disconnects (and to drain any keepalive pings).
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # a broken client must not crash the loop
        logger.exception("ws receive error")
    finally:
        await deps.manager.disconnect(websocket)
