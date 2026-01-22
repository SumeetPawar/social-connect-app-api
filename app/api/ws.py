from fastapi import APIRouter, WebSocket

router = APIRouter()

@router.websocket("/ws")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    await ws.close()
