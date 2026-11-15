"""Active bufferbloat stress test endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from ..engine.stress_test import StressTestBusy
from ..models import StressTestStatusOut

router = APIRouter(prefix="/api/stress-test", tags=["stress-test"])


@router.post("/start", response_model=StressTestStatusOut, status_code=status.HTTP_202_ACCEPTED)
async def start_stress_test(request: Request) -> StressTestStatusOut:
    service = request.app.state.service
    try:
        state = await service.stress.start(chaos_active=service.simulator.active)
    except StressTestBusy as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return StressTestStatusOut.model_validate(state)


@router.get("/status", response_model=StressTestStatusOut)
async def stress_test_status(request: Request) -> StressTestStatusOut:
    return StressTestStatusOut.model_validate(request.app.state.service.stress.status())
