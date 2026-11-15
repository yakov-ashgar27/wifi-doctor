"""
WiFi-Doctor - FastAPI entrypoint.

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import dispose_db, init_db
from .routers import control, incidents, stress
from .service import TelemetryService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)
log = logging.getLogger("wifi_doctor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    service = TelemetryService(settings)
    app.state.service = service
    await service.start()
    net = service.prober.network
    log.info(
        "WiFi-Doctor online | backend=%s gateway=%s dns=%s iface=%s",
        net.probe_backend, net.gateway_ip, net.dns_server, net.interface,
    )
    try:
        yield
    finally:
        await service.stop()
        await dispose_db()


app = FastAPI(
    title="WiFi-Doctor API",
    version=settings.version,
    description="Real-time network health monitoring and automated Root Cause Analysis.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(incidents.router)
app.include_router(control.router)
app.include_router(stress.router)


@app.websocket("/ws")
async def telemetry_ws(websocket: WebSocket) -> None:
    service: TelemetryService = websocket.app.state.service
    await service.register(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("websocket closed with error", exc_info=True)
    finally:
        service.unregister(websocket)


@app.get("/")
async def root() -> dict:
    return {"app": settings.app_name, "version": settings.version, "docs": "/docs", "ws": "/ws"}
