"""
TelemetryService: the real-time pipeline.

    Prober ─┐
            ├─> ChaosSimulator ─> Analyzer (RCA) ─┬─> WebSocket broadcast
    SystemMonitor ┘                               └─> IncidentRecorder ─> SQLite
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from fastapi import WebSocket

from .config import Settings
from .database import SessionLocal
from .engine.analyzer import Analyzer
from .engine.prober import Prober
from .engine.recorder import IncidentRecorder
from .engine.simulator import ChaosSimulator
from .engine.system_monitor import SystemMonitor

log = logging.getLogger("wifi_doctor.service")


class TelemetryService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.prober = Prober(settings)
        self.monitor = SystemMonitor(settings)
        self.analyzer = Analyzer(settings)
        self.simulator = ChaosSimulator()
        self.recorder = IncidentRecorder(SessionLocal, settings)
        self.clients: set[WebSocket] = set()
        self.history: deque[dict[str, Any]] = deque(maxlen=settings.history_frames)
        self.latest: dict[str, Any] | None = None
        self.started_at = time.time()
        self._task: asyncio.Task | None = None
        self._top_task: asyncio.Task | None = None
        self._last_top_refresh = 0.0

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.prober.initialize()
        self.monitor.set_interface(self.prober.network.interface)
        self.monitor.sample()  # prime counters
        await self.recorder.close_dangling()
        self._task = asyncio.create_task(self._run(), name="telemetry-loop")

    async def stop(self) -> None:
        for task in (self._task, self._top_task):
            if task:
                task.cancel()
        for task in (self._task, self._top_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        for ws in list(self.clients):
            try:
                await ws.close()
            except Exception:
                pass

    # ----------------------------------------------------------------- loop
    async def _run(self) -> None:
        interval = self.settings.probe_interval_s
        next_at = time.monotonic()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telemetry tick failed (continuing)")
            next_at += interval
            delay = next_at - time.monotonic()
            if delay < 0:  # we fell behind: skip missed slots instead of bursting
                next_at = time.monotonic() + interval
                delay = interval
            await asyncio.sleep(delay)

    def _maybe_refresh_top_process(self) -> None:
        now = time.monotonic()
        if now - self._last_top_refresh < self.settings.top_process_refresh_s:
            return
        if self._top_task and not self._top_task.done():
            return
        self._last_top_refresh = now

        async def _refresh() -> None:
            try:
                await asyncio.to_thread(self.monitor.refresh_top_process)
            except Exception:
                log.debug("top-process scan failed", exc_info=True)

        self._top_task = asyncio.create_task(_refresh())

    async def tick(self) -> dict[str, Any]:
        probes = await self.prober.probe_all()
        self.monitor.set_interface(self.prober.network.interface)
        bandwidth = self.monitor.sample()
        self._maybe_refresh_top_process()

        probes, bandwidth = self.simulator.apply(probes, bandwidth)
        frame = self.analyzer.ingest(probes, bandwidth, self.prober.network, self.simulator.mode.value)
        payload = frame.to_dict()
        self.latest = payload
        self.history.append(payload)

        await self.broadcast({"type": "telemetry", "frame": payload})
        event = await self.recorder.process(frame)
        if event:
            await self.broadcast({"type": "incident", **event})
        return payload

    # ------------------------------------------------------------ websocket
    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_text(json.dumps({"type": "snapshot", "frames": list(self.history)}))

    def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(message, default=str)

        async def _send(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=2.0)
                return None
            except Exception:
                return ws

        dead = await asyncio.gather(*(_send(ws) for ws in list(self.clients)))
        for ws in dead:
            if ws is not None:
                self.clients.discard(ws)
