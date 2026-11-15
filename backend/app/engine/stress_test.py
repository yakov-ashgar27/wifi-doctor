"""
Active Bufferbloat stress test: loaded vs. unloaded latency.

1. The unloaded baseline is the average gateway/WAN RTT from the last 5
   ticks before the test starts.
2. Concurrent streaming downloads (and an upload) saturate the link for
   ``stress_duration_s`` (8 s) while the normal 1.5 s prober keeps measuring.
3. Latency measured *under load* is compared to the baseline. The inflation
   is graded with the usual bufferbloat scale:

       < +5 ms A+ | < +30 ms A | < +60 ms B | < +200 ms C | < +400 ms D | else F

Safety: one run at a time (``StressTestBusy`` -> HTTP 409), a strict
``stress_timeout_s`` (10 s) watchdog, and transfer workers that stop at the
next chunk boundary. Networking uses only the standard library (urllib in
worker threads), so the test adds no dependencies and never blocks the loop.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
import urllib.request
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..config import Settings

log = logging.getLogger("wifi_doctor.stress")

USER_AGENT = "WiFi-Doctor/1.1 (+bufferbloat-test)"
CHUNK = 64 * 1024

GRADE_SCALE: list[tuple[float, str]] = [(5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")]


def grade_for_inflation(inflation_ms: float | None) -> str:
    """Map latency increase under load to a bufferbloat grade."""
    if inflation_ms is None:
        return "F"  # every probe under load was lost: as bad as it gets
    for limit, grade in GRADE_SCALE:
        if inflation_ms < limit:
            return grade
    return "F"


def needs_sqm(grade: str) -> bool:
    return grade in ("C", "D", "F")


SQM_RECOMMENDATION = (
    "Your router lets queues grow under load. Enable Smart Queue Management (SQM) with fq_codel or CAKE, "
    "set the shaper to about 85-95% of your measured speed, and turn on QoS for calls and games. "
    "Many ISP routers lack SQM; an OpenWrt, pfSense/OPNsense or 'gaming' router fixes it."
)


class StressTestBusy(Exception):
    """A test is already running (or chaos mode forbids one)."""


class TransferCounters:
    """Thread-safe byte counters shared by the transfer workers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.down = 0
        self.up = 0
        self.errors: list[str] = []
        self.endpoint: str | None = None

    def add(self, direction: str, n: int, url: str | None = None) -> None:
        with self._lock:
            if direction == "down":
                self.down += n
            else:
                self.up += n
            if url and self.endpoint is None:
                self.endpoint = url

    def error(self, msg: str) -> None:
        with self._lock:
            if len(self.errors) < 20:
                self.errors.append(msg)

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.down, self.up


TransferFn = Callable[[list[str], str, threading.Event, TransferCounters, float], None]


class _CountingBody:
    """File-like upload body that counts bytes as urllib reads them."""

    def __init__(self, size: int, stop: threading.Event, counters: TransferCounters, url: str):
        self.remaining = size
        self.stop = stop
        self.counters = counters
        self.url = url
        self._block = b"\0" * CHUNK

    def read(self, n: int = -1) -> bytes:
        if self.stop.is_set() or self.remaining <= 0:
            return b""
        size = CHUNK if n is None or n < 0 else min(n, CHUNK)
        size = min(size, self.remaining)
        self.remaining -= size
        self.counters.add("up", size, self.url)
        return self._block[:size]


def http_transfer(
    urls: list[str], direction: str, stop: threading.Event, counters: TransferCounters, timeout: float = 4.0
) -> None:
    """Blocking worker: stream from/to the first endpoint that works until ``stop``."""
    if not urls:
        return
    failures_in_a_row = 0
    for url in itertools.cycle(urls):
        if stop.is_set():
            return
        try:
            if direction == "down":
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoints
                    while not stop.is_set():
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        counters.add("down", len(chunk), url)
            else:
                size = 8 * 1024 * 1024
                body = _CountingBody(size, stop, counters, url)
                req = urllib.request.Request(
                    url,
                    data=body,  # type: ignore[arg-type]
                    method="POST",
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    resp.read(1024)
            failures_in_a_row = 0
        except Exception as exc:  # network errors, HTTP errors, truncated upload on stop...
            if stop.is_set():
                return
            failures_in_a_row += 1
            reason = getattr(exc, "reason", None) or getattr(exc, "code", None) or exc
            msg = f"{direction} {url.split('?')[0]}: {exc.__class__.__name__}: {reason}"[:240]
            counters.error(msg)
            log.warning("stress transfer failed: %s", msg)
            if failures_in_a_row >= len(urls) * 2:
                return  # every endpoint is unreachable; give up quietly
            stop.wait(0.2)


@dataclass
class StressTestResult:
    grade: str
    unloaded_gateway_ms: float | None
    unloaded_wan_ms: float | None
    loaded_gateway_median_ms: float | None
    loaded_gateway_p95_ms: float | None
    loaded_wan_median_ms: float | None
    loaded_wan_p95_ms: float | None
    inflation_ms: float | None
    inflation_multiplier: float | None
    peak_download_mbps: float
    peak_upload_mbps: float
    bytes_downloaded: int
    bytes_uploaded: int
    loaded_samples: int
    lost_samples: int
    endpoint: str | None
    recommendation: str | None
    reference_hop: str = "wan"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Sample:
    gateway: float | None
    wan: float | None


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def _avg(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(np.mean(vals)) if vals else None


def _median_p95(values: list[float | None]) -> tuple[float | None, float | None]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    arr = np.asarray(vals, dtype=float)
    return float(np.median(arr)), float(np.percentile(arr, 95))


def compute_result(
    unloaded: list[_Sample],
    loaded: list[_Sample],
    peak_down: float,
    peak_up: float,
    bytes_down: int,
    bytes_up: int,
    endpoint: str | None,
) -> StressTestResult:
    """Pure function: grade a finished test (unit-tested directly)."""
    un_gw = _avg([s.gateway for s in unloaded])
    un_wan = _avg([s.wan for s in unloaded])
    gw_med, gw_p95 = _median_p95([s.gateway for s in loaded])
    wan_med, wan_p95 = _median_p95([s.wan for s in loaded])

    # Internet latency is the user-facing number; fall back to the gateway.
    ref = "wan"
    base, under = un_wan, wan_med
    if base is None or (under is None and gw_med is not None):
        ref, base, under = "gateway", un_gw, gw_med

    lost = sum(1 for s in loaded if (s.wan if ref == "wan" else s.gateway) is None)
    inflation = max(0.0, under - base) if (under is not None and base is not None) else None
    multiplier = (under / base) if (under is not None and base and base > 0) else None
    if not loaded or base is None:
        grade = "?"  # not enough data to grade honestly
    else:
        grade = grade_for_inflation(inflation)  # None here means every loaded probe was lost -> F
    return StressTestResult(
        grade=grade,
        unloaded_gateway_ms=_r(un_gw),
        unloaded_wan_ms=_r(un_wan),
        loaded_gateway_median_ms=_r(gw_med),
        loaded_gateway_p95_ms=_r(gw_p95),
        loaded_wan_median_ms=_r(wan_med),
        loaded_wan_p95_ms=_r(wan_p95),
        inflation_ms=_r(inflation),
        inflation_multiplier=_r(multiplier, 2),
        peak_download_mbps=round(peak_down, 1),
        peak_upload_mbps=round(peak_up, 1),
        bytes_downloaded=bytes_down,
        bytes_uploaded=bytes_up,
        loaded_samples=len(loaded),
        lost_samples=lost,
        endpoint=endpoint,
        recommendation=SQM_RECOMMENDATION if needs_sqm(grade) else None,
        reference_hop=ref,
    )


@dataclass
class _RunState:
    test_id: str
    started_wall: float
    started_mono: float
    unloaded: list[_Sample]
    loaded: list[_Sample] = field(default_factory=list)


class StressTestRunner:
    def __init__(
        self,
        settings: Settings,
        frames: Callable[[], list[dict[str, Any]]],
        broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        transfer: TransferFn | None = None,
        duration_s: float | None = None,
        timeout_s: float | None = None,
        warmup_s: float = 1.0,
    ):
        self.s = settings
        self._frames = frames
        self._broadcast = broadcast
        self._transfer = transfer or http_transfer
        self.duration_s = duration_s if duration_s is not None else settings.stress_duration_s
        self.timeout_s = timeout_s if timeout_s is not None else settings.stress_timeout_s
        self.warmup_s = warmup_s
        self.state = "idle"  # idle | running | completed | failed | aborted
        self.progress = 0.0
        self.live_download_mbps = 0.0
        self.live_upload_mbps = 0.0
        self.result: StressTestResult | None = None
        self.error: str | None = None
        self.finished_at: float | None = None
        self._run: _RunState | None = None
        self.diagnostics: list[str] = []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.state == "running"

    # ------------------------------------------------------------------ api
    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "test_id": self._run.test_id if self._run else None,
            "started_at": self._run.started_wall if self._run else None,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "progress": round(self.progress, 3),
            "live_download_mbps": round(self.live_download_mbps, 1),
            "live_upload_mbps": round(self.live_upload_mbps, 1),
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "diagnostics": list(self.diagnostics),
        }

    async def start(self, chaos_active: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                raise StressTestBusy("A bufferbloat test is already running.")
            if chaos_active:
                raise StressTestBusy("Disable the chaos simulation before running a real stress test.")
            unloaded = [self._sample(f) for f in self._frames()[-5:]]
            self._run = _RunState(uuid.uuid4().hex[:8], time.time(), time.monotonic(), unloaded)
            self.state, self.progress, self.result, self.error, self.finished_at = "running", 0.0, None, None, None
            self.diagnostics = []
            self.live_download_mbps = self.live_upload_mbps = 0.0
            self._task = asyncio.create_task(self._execute(), name="bufferbloat-test")
        await self._emit()
        return self.status()

    def on_frame(self, frame: dict[str, Any]) -> None:
        """Called by the telemetry loop every tick; keeps the samples taken under load."""
        run = self._run
        if not self.running or run is None:
            return
        if time.monotonic() - run.started_mono >= self.warmup_s:
            run.loaded.append(self._sample(frame))

    async def wait(self) -> None:
        if self._task:
            await asyncio.shield(self._task)

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------- internals
    @staticmethod
    def _sample(frame: dict[str, Any]) -> _Sample:
        hops = frame.get("hops", {})
        return _Sample(
            gateway=(hops.get("gateway") or {}).get("current_ms"),
            wan=(hops.get("wan") or {}).get("current_ms"),
        )

    async def _emit(self) -> None:
        if self._broadcast:
            try:
                await self._broadcast({"type": "stress_test", "status": self.status()})
            except Exception:
                log.debug("stress broadcast failed", exc_info=True)

    async def _execute(self) -> None:
        stop = threading.Event()
        counters = TransferCounters()
        timeout = max(1.0, self.s.probe_timeout_s * 4)
        workers = [
            asyncio.create_task(asyncio.to_thread(self._transfer, list(self.s.stress_download_urls), "down", stop, counters, timeout))
            for _ in range(max(1, self.s.stress_download_workers))
        ] + [
            asyncio.create_task(asyncio.to_thread(self._transfer, list(self.s.stress_upload_urls), "up", stop, counters, timeout))
            for _ in range(max(0, self.s.stress_upload_workers))
        ]
        peaks = {"down": 0.0, "up": 0.0}
        try:
            await asyncio.wait_for(self._drive(counters, peaks), timeout=self.timeout_s)
            down, up = counters.snapshot()
            if down + up == 0:
                self.state = "failed"
                self.error = "Could not reach any speed-test endpoint (offline, firewalled or blocked)."
            else:
                assert self._run is not None
                self.result = compute_result(
                    self._run.unloaded, self._run.loaded, peaks["down"], peaks["up"], down, up, counters.endpoint
                )
                self.state = "completed"
        except asyncio.TimeoutError:
            self.state = "aborted"
            self.error = f"Aborted by the {self.timeout_s:.0f}s safety timeout."
        except asyncio.CancelledError:
            self.state = "aborted"
            self.error = "Cancelled."
            raise
        except Exception as exc:  # never let a test crash the service
            log.exception("bufferbloat test failed")
            self.state = "failed"
            self.error = f"{exc.__class__.__name__}: {exc}"
        finally:
            stop.set()
            self.diagnostics = list(dict.fromkeys(counters.errors))[:5]
            self.progress = 1.0 if self.state == "completed" else self.progress
            self.live_download_mbps = self.live_upload_mbps = 0.0
            self.finished_at = time.time()
            # Workers notice `stop` at the next chunk; don't wait on slow sockets.
            await asyncio.wait(workers, timeout=1.5)
            await self._emit()
            if self.result:
                log.info("bufferbloat test %s: grade %s (+%s ms)", self._run.test_id if self._run else "?",
                         self.result.grade, self.result.inflation_ms)

    async def _drive(self, counters: TransferCounters, peaks: dict[str, float]) -> None:
        assert self._run is not None
        start = self._run.started_mono
        window: deque[tuple[float, int, int]] = deque()
        while True:
            now = time.monotonic()
            elapsed = now - start
            down, up = counters.snapshot()
            window.append((now, down, up))
            while window and now - window[0][0] > 1.0:
                window.popleft()
            if len(window) >= 2 and window[-1][0] > window[0][0]:
                span = window[-1][0] - window[0][0]
                self.live_download_mbps = (window[-1][1] - window[0][1]) * 8 / 1e6 / span
                self.live_upload_mbps = (window[-1][2] - window[0][2]) * 8 / 1e6 / span
                if span >= 0.45:
                    peaks["down"] = max(peaks["down"], self.live_download_mbps)
                    peaks["up"] = max(peaks["up"], self.live_upload_mbps)
            self.progress = min(1.0, elapsed / self.duration_s)
            await self._emit()
            if elapsed >= self.duration_s:
                return
            await asyncio.sleep(min(0.5, max(0.05, self.duration_s - elapsed)))
