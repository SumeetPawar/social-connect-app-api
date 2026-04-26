"""
WebSocket endpoint — real-time partner chat delivery via PostgreSQL LISTEN/NOTIFY.

Flow:
  1. Client connects: WS /ws?token=<JWT>
  2. JWT validated → user_id extracted
  3. User registered in _online dict
  4. asyncpg connection starts LISTEN on channel "user_<user_id>"
  5. Any pg_notify on that channel → forwarded to WebSocket
  6. On disconnect: UNLISTEN, removed from _online

Online check:
  partners.py uses `is_online(user_id)` before deciding push vs WS delivery.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# user_id (str) → active WebSocket
_online: Dict[str, WebSocket] = {}


def is_online(user_id: str) -> bool:
    """Return True if the user currently has an open WebSocket connection."""
    return user_id in _online


async def notify_user(user_id: str, payload: dict) -> bool:
    """
    Deliver a message to a connected user via WebSocket.
    Returns True if delivered, False if user not online.
    """
    ws = _online.get(user_id)
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        _online.pop(user_id, None)
        return False


def _validate_token(token: str) -> str | None:
    """Decode JWT and return user_id (sub), or None if invalid."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        if payload.get("type") != "access":
            return None
        return payload.get("sub")
    except JWTError:
        return None


async def _listen_and_forward(user_id: str, ws: WebSocket) -> None:
    """
    Open a dedicated asyncpg connection, LISTEN on "user_<user_id>",
    and forward every notification payload to the WebSocket.
    Exits when the WebSocket closes or the listen loop breaks.
    """
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn)
        channel = f"user_{user_id}"

        async def on_notify(connection, pid, channel, payload):
            try:
                await ws.send_text(payload)
            except Exception:
                pass  # ws already closed; outer loop will exit

        await conn.add_listener(channel, on_notify)

        # Keep alive until WebSocket disconnects (detected by receive loop)
        while user_id in _online:
            await asyncio.sleep(1)

    except Exception as exc:
        logger.warning("LISTEN loop error for user %s: %s", user_id, exc)
    finally:
        if conn:
            try:
                await conn.remove_listener(f"user_{user_id}", on_notify)
                await conn.close()
            except Exception:
                pass


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    """
    WebSocket endpoint for real-time partner chat.
    Query param: ?token=<JWT access token>
    """
    # 1. Validate JWT before accepting
    user_id = _validate_token(token)
    if not user_id:
        await ws.close(code=4001)
        return

    await ws.accept()
    _online[user_id] = ws
    logger.info("WS connected: user=%s  online=%d", user_id, len(_online))

    # 2. Start LISTEN/NOTIFY forwarding in background
    listen_task = asyncio.create_task(_listen_and_forward(user_id, ws))

    try:
        # 3. Keep the connection open; receive loop for client messages (ping/pong)
        while True:
            data = await ws.receive_text()
            # Clients may send {"type":"ping"} — respond with pong
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass

    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _online.pop(user_id, None)
        listen_task.cancel()
        logger.info("WS disconnected: user=%s  online=%d", user_id, len(_online))
