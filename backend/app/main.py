"""ColdChain backend entry point.

Run from the project root:
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, events, services
from .ai import predictor
from .config import ROOT
from .engine import run_ai_cycle
from .ingest import Ingestor, seed_devices
from .routers.api import router as api_router
from .security import decode_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("coldchain")
FRONTEND = ROOT / "frontend"


# ------------------------------------------------------------------ live push (WebSocket)
class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def _send_all(self, msg: str) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                self.clients.discard(ws)

    def push(self, event: dict) -> None:  # called from MQTT / engine threads
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self._send_all(json.dumps(event, default=str)), self.loop)


hub = Hub()


def _background(ingestor: Ingestor, stop: threading.Event, period: int) -> None:
    last_ai = 0.0
    while not stop.is_set():
        now = time.time()
        try:
            ingestor.watchdog()
            if now - last_ai >= period:
                last_ai = now
                run_ai_cycle(now)
        except Exception:
            log.exception("background cycle failed")
        stop.wait(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import settings
    db.init_schema()
    with db.tx() as conn:
        seed_devices(conn, time.time())
    services.ensure_admin(time.time())
    predictor.load_model()
    log.info("model: %s", predictor.model_info())

    ingestor = Ingestor()
    app.state.ingestor = ingestor
    hub.loop = asyncio.get_running_loop()
    events.subscribe(hub.push)

    bridge = None
    if os.environ.get("CC_MQTT_ENABLED", "1") == "1":
        from .mqtt_bridge import MqttBridge
        bridge = MqttBridge(ingestor)
        bridge.start()
    stop = threading.Event()
    t = threading.Thread(target=_background, args=(ingestor, stop, settings.ai_period_s), daemon=True)
    t.start()
    yield
    stop.set()
    if bridge:
        bridge.stop()


app = FastAPI(title="ColdChain", version="1.0.0", lifespan=lifespan)
app.include_router(api_router)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/api/health")
def health():
    return {"ok": True, "time": time.time()}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    try:
        decode_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep-alive pings from the browser
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(websocket)


@app.get("/check/{public_id}")
def check_page(public_id: str):
    return FileResponse(FRONTEND / "check.html")


# AI report figures (no sensitive data) shown on the AI page
app.mount("/report", StaticFiles(directory=str(ROOT / "docs"), check_dir=False), name="report")
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
