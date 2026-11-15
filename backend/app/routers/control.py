"""Chaos control + live status endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from ..engine.simulator import ChaosMode
from ..models import ChaosState

router = APIRouter(prefix="/api", tags=["control"])

MODES = [m.value for m in ChaosMode]


def _service(request: Request):
    return request.app.state.service


@router.get("/chaos", response_model=ChaosState)
async def get_chaos(request: Request) -> ChaosState:
    sim = _service(request).simulator
    return ChaosState(mode=sim.mode.value, since=sim.since, available=MODES)


@router.post("/chaos/{mode}", response_model=ChaosState)
async def set_chaos(mode: str, request: Request) -> ChaosState:
    if mode not in MODES:
        raise HTTPException(status_code=422, detail=f"Unknown chaos mode '{mode}'. Use one of: {', '.join(MODES)}")
    service = _service(request)
    service.simulator.set_mode(mode)
    await service.broadcast({"type": "chaos", "mode": mode, "since": service.simulator.since})
    return ChaosState(mode=mode, since=service.simulator.since, available=MODES)


@router.get("/status")
async def status(request: Request) -> dict:
    service = _service(request)
    return {
        "app": "WiFi-Doctor",
        "uptime_s": round(time.time() - service.started_at, 1),
        "clients": len(service.clients),
        "chaos_mode": service.simulator.mode.value,
        "network": service.prober.network.to_dict(),
        "latest": service.latest,
    }


@router.get("/health")
async def health() -> dict:
    return {"ok": True}
