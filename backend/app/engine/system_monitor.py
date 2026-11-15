"""
Local bandwidth & process monitor.

* Throughput (Mbps) from ``psutil.net_io_counters`` deltas on the active NIC.
* Observed peak bandwidth over a ~10 minute window (used for bufferbloat
  utilisation), floored so an idle host never looks "saturated".
* Best-effort "top network process": processes owning live Internet sockets,
  ranked by their I/O rate. Every OS restricts this differently, so every
  ``AccessDenied`` / ``NoSuchProcess`` is handled and the feature simply
  degrades to connection counts (or ``None``) instead of failing.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from ..config import Settings

log = logging.getLogger("wifi_doctor.system")

_LOOPBACK_HINTS = ("lo", "loopback", "docker", "veth", "br-", "vmnet", "vboxnet", "utun", "awdl", "llw")
_WIRELESS_HINTS = ("wlan", "wlp", "wl", "wi-fi", "wifi", "wireless", "airport")


@dataclass
class BandwidthSnapshot:
    download_mbps: float
    upload_mbps: float
    peak_download_mbps: float
    peak_upload_mbps: float
    interface: str | None
    link_speed_mbps: float | None
    is_wireless: bool | None
    top_process: dict[str, Any] | None
    simulated: bool = False

    @property
    def utilization_pct(self) -> float:
        dl = self.download_mbps / self.peak_download_mbps if self.peak_download_mbps else 0.0
        ul = self.upload_mbps / self.peak_upload_mbps if self.peak_upload_mbps else 0.0
        return round(min(max(dl, ul), 1.0) * 100.0, 1)

    @property
    def total_mbps(self) -> float:
        return self.download_mbps + self.upload_mbps

    def to_dict(self) -> dict[str, Any]:
        return {
            "download_mbps": round(self.download_mbps, 3),
            "upload_mbps": round(self.upload_mbps, 3),
            "peak_download_mbps": round(self.peak_download_mbps, 2),
            "peak_upload_mbps": round(self.peak_upload_mbps, 2),
            "utilization_pct": self.utilization_pct,
            "interface": self.interface,
            "link_speed_mbps": self.link_speed_mbps,
            "is_wireless": self.is_wireless,
            "top_process": self.top_process,
            "simulated": self.simulated,
        }


def _is_wireless(nic: str | None) -> bool | None:
    if not nic:
        return None
    if Path(f"/sys/class/net/{nic}/wireless").exists() or Path(f"/sys/class/net/{nic}/phy80211").exists():
        return True
    lowered = nic.lower()
    if any(lowered.startswith(h) or h in lowered for h in _WIRELESS_HINTS):
        return True
    if lowered.startswith(("eth", "en", "ethernet")) and not lowered.startswith("en0"):
        return False
    return None


class SystemMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.interface: str | None = None
        self._last_counters: tuple[int, int] | None = None
        self._last_time: float | None = None
        self._dl_hist: deque[float] = deque(maxlen=settings.peak_window_samples)
        self._ul_hist: deque[float] = deque(maxlen=settings.peak_window_samples)
        self._top_process: dict[str, Any] | None = None
        self._io_prev: dict[int, tuple[int, float]] = {}
        self._own_pid = os.getpid()

    # ------------------------------------------------------------- counters
    def set_interface(self, nic: str | None) -> None:
        if nic != self.interface:
            self.interface = nic
            self._last_counters = None  # avoid a bogus spike when switching NICs

    def _read_counters(self) -> tuple[int, int]:
        try:
            per_nic = psutil.net_io_counters(pernic=True)
        except Exception:
            per_nic = {}
        if self.interface and self.interface in per_nic:
            c = per_nic[self.interface]
            return c.bytes_recv, c.bytes_sent
        recv = sent = 0
        for name, c in per_nic.items():
            if name.lower().startswith(_LOOPBACK_HINTS):
                continue
            recv += c.bytes_recv
            sent += c.bytes_sent
        if not per_nic:
            total = psutil.net_io_counters()
            return total.bytes_recv, total.bytes_sent
        return recv, sent

    def _link_speed(self) -> float | None:
        try:
            stats = psutil.net_if_stats().get(self.interface or "")
            return float(stats.speed) if stats and stats.speed else None
        except Exception:
            return None

    def sample(self) -> BandwidthSnapshot:
        now = time.monotonic()
        recv, sent = self._read_counters()
        dl = ul = 0.0
        if self._last_counters is not None and self._last_time is not None:
            elapsed = max(now - self._last_time, 1e-3)
            d_recv = max(recv - self._last_counters[0], 0)  # handles counter resets/wraps
            d_sent = max(sent - self._last_counters[1], 0)
            dl = d_recv * 8 / 1_000_000 / elapsed
            ul = d_sent * 8 / 1_000_000 / elapsed
            self._dl_hist.append(dl)
            self._ul_hist.append(ul)
        self._last_counters = (recv, sent)
        self._last_time = now

        floor = self.settings.peak_floor_mbps
        peak_dl = max(max(self._dl_hist, default=0.0), floor)
        peak_ul = max(max(self._ul_hist, default=0.0), floor)
        return BandwidthSnapshot(
            download_mbps=dl,
            upload_mbps=ul,
            peak_download_mbps=peak_dl,
            peak_upload_mbps=peak_ul,
            interface=self.interface,
            link_speed_mbps=self._link_speed(),
            is_wireless=_is_wireless(self.interface),
            top_process=self._top_process,
        )

    # ---------------------------------------------------------- top process
    @staticmethod
    def _proc_connections(proc: psutil.Process) -> list:
        getter = getattr(proc, "net_connections", None) or getattr(proc, "connections")
        return getter(kind="inet")

    def _connection_counts(self) -> Counter:
        counts: Counter = Counter()
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.pid and conn.raddr and (
                    conn.status == psutil.CONN_ESTABLISHED or conn.type == socket.SOCK_DGRAM
                ):
                    counts[conn.pid] += 1
            return counts
        except (psutil.AccessDenied, PermissionError, OSError):
            pass  # macOS / restricted containers: walk processes individually
        for proc in psutil.process_iter(["pid"]):
            try:
                n = sum(
                    1 for c in self._proc_connections(proc)
                    if c.raddr and c.status == psutil.CONN_ESTABLISHED
                )
                if n:
                    counts[proc.pid] = n
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                continue
        return counts

    @staticmethod
    def _io_bytes(proc: psutil.Process) -> int | None:
        try:
            io = proc.io_counters()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, AttributeError, OSError):
            return None
        # Linux: read_chars/write_chars include socket traffic.
        # Windows: other_bytes covers non-disk (network/device) I/O.
        for attrs in (("read_chars", "write_chars"), ("other_bytes",), ("read_bytes", "write_bytes")):
            if all(hasattr(io, a) for a in attrs):
                return int(sum(getattr(io, a) for a in attrs))
        return None

    def refresh_top_process(self) -> dict[str, Any] | None:
        """Blocking scan - call from a worker thread every few seconds."""
        counts = self._connection_counts()
        counts.pop(self._own_pid, None)
        now = time.monotonic()
        best: tuple[float, int, int, str] | None = None  # (rate, conns, pid, name)
        seen: dict[int, tuple[int, float]] = {}
        for pid, conns in counts.most_common(40):
            try:
                proc = psutil.Process(pid)
                name = proc.name()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            rate = 0.0
            total = self._io_bytes(proc)
            if total is not None:
                seen[pid] = (total, now)
                prev = self._io_prev.get(pid)
                if prev and now > prev[1]:
                    rate = max(total - prev[0], 0) * 8 / 1_000_000 / (now - prev[1])
            key = (rate, conns, pid, name)
            if best is None or key[:2] > best[:2]:
                best = key
        self._io_prev = seen
        if best is None:
            self._top_process = None
        else:
            rate, conns, pid, name = best
            self._top_process = {
                "name": name,
                "pid": pid,
                "connections": conns,
                "io_mbps": round(rate, 2) if rate > 0 else None,
            }
        return self._top_process
