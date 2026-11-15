"""
Incident lifecycle: turns the per-tick verdict stream into IncidentLog rows.

* Opens an incident when status leaves HEALTHY (subject to a 30 s cooldown
  after the previous incident opened, so a flapping link cannot spam the DB).
* While open, tracks the *worst* metrics seen and escalates severity / root
  cause if a more severe diagnosis appears. Only escalations touch the DB.
* Closes the incident after N consecutive HEALTHY ticks and stores duration.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..models import IncidentLog, IncidentOut
from .analyzer import CRITICAL, HEALTHY, TelemetryFrame

log = logging.getLogger("wifi_doctor.incidents")

_SEVERITY_RANK = {HEALTHY: 0, "WARNING": 1, CRITICAL: 2}


def _max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


class IncidentRecorder:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings):
        self.sessions = session_factory
        self.s = settings
        self.open_id: int | None = None
        self.open_started: datetime | None = None
        self.last_opened_at: float = 0.0
        self._healthy_streak = 0
        self._peak: dict[str, Any] = {}
        self._severity = HEALTHY
        self._confidence = 0

    async def close_dangling(self) -> None:
        """Incidents left open by a previous crash/shutdown get closed on boot."""
        async with self.sessions() as session:
            await session.execute(
                update(IncidentLog)
                .where(IncidentLog.resolved_at.is_(None))
                .values(resolved_at=datetime.now(timezone.utc))
            )
            await session.commit()

    # ------------------------------------------------------------------ api
    async def process(self, frame: TelemetryFrame, suppress: bool = False) -> dict[str, Any] | None:
        """Returns a broadcastable event dict when an incident opens/escalates/closes.

        ``suppress`` is set while WiFi-Doctor itself is loading the link (bufferbloat
        test): that degradation is self-inflicted and must not reach the ISP report.
        """
        if suppress:
            return None
        d = frame.diagnosis
        try:
            if d.status == HEALTHY:
                if self.open_id is None:
                    return None
                self._healthy_streak += 1
                if self._healthy_streak >= self.s.incident_recovery_ticks:
                    return await self._close()
                return None

            self._healthy_streak = 0
            if self.open_id is None:
                if time.time() - self.last_opened_at < self.s.incident_cooldown_s:
                    return None
                return await self._open(frame)
            return await self._update(frame)
        except Exception:  # DB trouble must never stop telemetry
            log.exception("incident recorder failure")
            return None

    # -------------------------------------------------------------- helpers
    def _metrics(self, frame: TelemetryFrame) -> dict[str, Any]:
        h, bw = frame.hops, frame.bandwidth
        return {
            "gateway_rtt_ms": h["gateway"].recent_ms,
            "gateway_loss_pct": h["gateway"].recent_loss_pct,
            "wan_rtt_ms": h["wan"].recent_ms,
            "wan_jitter_ms": h["wan"].jitter_ms,
            "wan_loss_pct": h["wan"].recent_loss_pct,
            "dns_ms": h["dns"].recent_ms,
            "dns_loss_pct": h["dns"].recent_loss_pct,
            "download_mbps": round(bw.download_mbps, 2),
            "upload_mbps": round(bw.upload_mbps, 2),
        }

    def _merge_peak(self, frame: TelemetryFrame) -> None:
        for key, value in self._metrics(frame).items():
            self._peak[key] = _max(self._peak.get(key), value)
        top = (frame.bandwidth.top_process or {}).get("name")
        if top:
            self._peak["top_process"] = top

    async def _open(self, frame: TelemetryFrame) -> dict[str, Any]:
        d, net = frame.diagnosis, frame.network
        self._peak = {}
        self._merge_peak(frame)
        self._severity, self._confidence = d.status, d.confidence_score
        row = IncidentLog(
            started_at=datetime.fromtimestamp(frame.timestamp, timezone.utc),
            severity=d.status,
            root_cause_layer=d.root_cause_layer,
            confidence_score=d.confidence_score,
            title=d.title,
            explanation=d.explanation,
            recommended_action=d.recommended_action,
            gateway_ip=net.gateway_ip,
            wan_target=frame.hops["wan"].target,
            dns_server=net.dns_server,
            probe_backend=net.probe_backend,
            simulated=frame.chaos_mode != "none",
            chaos_mode=frame.chaos_mode,
            **self._peak,
        )
        async with self.sessions() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        self.open_id, self.open_started = row.id, row.started_at
        self.last_opened_at = time.time()
        log.warning("incident #%s opened: %s (%s)", row.id, d.title, d.root_cause_layer)
        return {"event": "opened", "incident": IncidentOut.model_validate(row).model_dump(mode="json")}

    async def _update(self, frame: TelemetryFrame) -> dict[str, Any] | None:
        d = frame.diagnosis
        self._merge_peak(frame)
        escalate = _SEVERITY_RANK[d.status] > _SEVERITY_RANK[self._severity] or (
            d.status == self._severity and d.confidence_score >= self._confidence + 10
        )
        if not escalate:
            return None
        self._severity, self._confidence = d.status, d.confidence_score
        values = {
            "severity": d.status,
            "root_cause_layer": d.root_cause_layer,
            "confidence_score": d.confidence_score,
            "title": d.title,
            "explanation": d.explanation,
            "recommended_action": d.recommended_action,
            **self._peak,
        }
        if frame.chaos_mode != "none":
            values.update(simulated=True, chaos_mode=frame.chaos_mode)
        row = await self._write(values)
        return {"event": "updated", "incident": row} if row else None

    async def _close(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        started = self.open_started or now
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        values = {"resolved_at": now, "duration_s": round((now - started).total_seconds(), 1), **self._peak}
        row = await self._write(values)
        log.info("incident #%s resolved after %.0fs", self.open_id, values["duration_s"])
        self.open_id = None
        self.open_started = None
        self._healthy_streak = 0
        self._severity = HEALTHY
        return {"event": "closed", "incident": row} if row else None

    async def _write(self, values: dict[str, Any]) -> dict[str, Any] | None:
        async with self.sessions() as session:
            await session.execute(update(IncidentLog).where(IncidentLog.id == self.open_id).values(**values))
            await session.commit()
            row = (await session.execute(select(IncidentLog).where(IncidentLog.id == self.open_id))).scalar_one_or_none()
            return IncidentOut.model_validate(row).model_dump(mode="json") if row else None
