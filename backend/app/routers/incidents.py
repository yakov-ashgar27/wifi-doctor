"""Incident history + ISP evidence report (CSV)."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import IncidentLog, IncidentOut, IncidentPage

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

CSV_COLUMNS = [
    ("incident_id", "id"),
    ("started_at_utc", "started_at"),
    ("resolved_at_utc", "resolved_at"),
    ("duration_s", "duration_s"),
    ("severity", "severity"),
    ("root_cause_layer", "root_cause_layer"),
    ("confidence_pct", "confidence_score"),
    ("diagnosis", "title"),
    ("gateway_ip", "gateway_ip"),
    ("worst_gateway_rtt_ms", "gateway_rtt_ms"),
    ("worst_gateway_loss_pct", "gateway_loss_pct"),
    ("wan_target", "wan_target"),
    ("worst_wan_rtt_ms", "wan_rtt_ms"),
    ("worst_wan_jitter_ms", "wan_jitter_ms"),
    ("worst_wan_loss_pct", "wan_loss_pct"),
    ("dns_server", "dns_server"),
    ("worst_dns_ms", "dns_ms"),
    ("worst_dns_loss_pct", "dns_loss_pct"),
    ("peak_download_mbps", "download_mbps"),
    ("peak_upload_mbps", "upload_mbps"),
    ("top_process", "top_process"),
    ("probe_method", "probe_backend"),
    ("simulated", "simulated"),
    ("explanation", "explanation"),
    ("recommended_action", "recommended_action"),
]


def _filtered(stmt, layer: str | None, real_only: bool):
    if layer:
        stmt = stmt.where(IncidentLog.root_cause_layer == layer.upper())
    if real_only:
        stmt = stmt.where(IncidentLog.simulated.is_(False))
    return stmt


@router.get("", response_model=IncidentPage)
async def list_incidents(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    layer: str | None = Query(None, description="Filter by root cause layer"),
    real_only: bool = Query(False, description="Exclude chaos-simulated incidents"),
    session: AsyncSession = Depends(get_session),
) -> IncidentPage:
    total = (await session.execute(_filtered(select(func.count(IncidentLog.id)), layer, real_only))).scalar_one()
    rows = (
        await session.execute(
            _filtered(select(IncidentLog), layer, real_only)
            .order_by(IncidentLog.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return IncidentPage(
        items=[IncidentOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    text = str(value)
    # Neutralise spreadsheet formula injection (=, +, -, @ at cell start).
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export")
async def export_csv(
    real_only: bool = Query(False, description="Exclude chaos-simulated incidents"),
    layer: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    rows = (
        await session.execute(_filtered(select(IncidentLog), layer, real_only).order_by(IncidentLog.started_at.asc()))
    ).scalars().all()

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([name for name, _ in CSV_COLUMNS])
        yield "﻿" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly
        for row in rows:
            buf.seek(0)
            buf.truncate(0)
            writer.writerow([_cell(getattr(row, attr)) for _, attr in CSV_COLUMNS])
            yield buf.getvalue()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="wifi-doctor-isp-report-{stamp}.csv"'},
    )
