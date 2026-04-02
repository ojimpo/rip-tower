"""WebSocket connection management and event broadcasting."""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

_connections: list[WebSocket] = []


async def broadcast(event_type: str, data: dict[str, Any]) -> None:
    """Send an event to all connected WebSocket clients."""
    message = json.dumps({"type": event_type, **data})
    disconnected = []
    for ws in _connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        _connections.remove(ws)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.append(websocket)
    logger.info("WebSocket client connected (%d total)", len(_connections))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.remove(websocket)
        logger.info("WebSocket client disconnected (%d total)", len(_connections))
