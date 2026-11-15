"""
Chaos Engineering / Fault Injection.

The simulator sits between the real probes and the Analyzer. Real
measurements keep flowing; the active fault is layered *on top* of them, the
way a real fault would add delay or drop packets on one segment of the path.
Faults ramp in over ~3 ticks so charts show a realistic onset rather than a
step function.

Modes
-----
none          pass-through
wifi_lag      +40..160 ms and 8 % loss on the first hop (inherited by WAN & DNS)
isp_loss      +110..260 ms and 18 % loss beyond the gateway (WAN, and DNS rides on it)
dns_slowdown  +220..900 ms resolver latency and 12 % DNS timeouts
bufferbloat   synthetic ~95 % link saturation + queueing delay on every hop
"""

from __future__ import annotations

import random
import time
from dataclasses import replace
from enum import Enum

from .prober import HOP_DNS, HOP_GATEWAY, HOP_WAN, ProbeSample
from .system_monitor import BandwidthSnapshot


class ChaosMode(str, Enum):
    NONE = "none"
    WIFI_LAG = "wifi_lag"
    ISP_LOSS = "isp_loss"
    DNS_SLOWDOWN = "dns_slowdown"
    BUFFERBLOAT = "bufferbloat"


class ChaosSimulator:
    RAMP_TICKS = 3
    # Synthetic link used for the bufferbloat scenario (Mbps)
    SIM_LINK_DOWN = 100.0
    SIM_LINK_UP = 20.0

    def __init__(self, seed: int | None = None):
        self.mode = ChaosMode.NONE
        self.since: float | None = None
        self._ticks = 0
        self._rng = random.Random(seed)

    def set_mode(self, mode: ChaosMode | str) -> ChaosMode:
        mode = ChaosMode(mode)
        if mode != self.mode:
            self.mode = mode
            self.since = time.time() if mode != ChaosMode.NONE else None
            self._ticks = 0
        return self.mode

    @property
    def active(self) -> bool:
        return self.mode != ChaosMode.NONE

    # ------------------------------------------------------------- helpers
    def _intensity(self) -> float:
        return min(1.0, (self._ticks + 1) / self.RAMP_TICKS)

    def _add(self, s: ProbeSample, extra_ms: float, loss: float) -> ProbeSample:
        if s.target is None:
            return s
        if self._rng.random() < loss or s.rtt_ms is None:
            # A lost real probe stays lost; otherwise drop with probability `loss`.
            return replace(s, rtt_ms=None, error="simulated-loss", simulated=True)
        return replace(s, rtt_ms=s.rtt_ms + max(extra_ms, 0.0), simulated=True)

    def _u(self, lo: float, hi: float) -> float:
        return self._rng.uniform(lo, hi)

    # --------------------------------------------------------------- apply
    def apply(
        self, probes: dict[str, ProbeSample], bw: BandwidthSnapshot
    ) -> tuple[dict[str, ProbeSample], BandwidthSnapshot]:
        if self.mode == ChaosMode.NONE:
            return probes, bw
        k = self._intensity()
        self._ticks += 1
        gw, wan, dns = probes[HOP_GATEWAY], probes[HOP_WAN], probes[HOP_DNS]

        if self.mode == ChaosMode.WIFI_LAG:
            # Radio retransmissions: bursty, high-variance delay on the first hop
            # that every later hop inherits.
            local = self._u(40, 160) * k
            gw = self._add(gw, local, 0.08 * k)
            wan = self._add(wan, local + self._u(0, 15), 0.08 * k)
            dns = self._add(dns, local * 0.6, 0.03 * k)

        elif self.mode == ChaosMode.ISP_LOSS:
            upstream = self._u(110, 260) * k
            gw = self._add(gw, self._u(0, 1.5), 0.0)
            wan = self._add(wan, upstream, 0.18 * k)
            dns = self._add(dns, upstream * 0.3, 0.02 * k)

        elif self.mode == ChaosMode.DNS_SLOWDOWN:
            gw = self._add(gw, 0.0, 0.0)
            wan = self._add(wan, self._u(0, 4), 0.0)
            dns = self._add(dns, self._u(220, 900) * k, 0.12 * k)

        elif self.mode == ChaosMode.BUFFERBLOAT:
            queue = self._u(45, 140) * k  # standing queue in the router buffer
            gw = self._add(gw, queue, 0.01 * k)
            wan = self._add(wan, queue + self._u(30, 90) * k, 0.02 * k)
            dns = self._add(dns, queue * 0.7, 0.01 * k)
            sat_down = self._u(0.86, 0.98) * k
            sat_up = self._u(0.80, 0.97) * k
            bw = replace(
                bw,
                download_mbps=bw.download_mbps + self.SIM_LINK_DOWN * sat_down,
                upload_mbps=bw.upload_mbps + self.SIM_LINK_UP * sat_up,
                peak_download_mbps=max(bw.peak_download_mbps, self.SIM_LINK_DOWN),
                peak_upload_mbps=max(bw.peak_upload_mbps, self.SIM_LINK_UP),
                top_process={"name": "chaos-bulk-transfer", "pid": 0, "connections": 16,
                             "io_mbps": round(self.SIM_LINK_DOWN * sat_down, 1)},
                simulated=True,
            )

        return {HOP_GATEWAY: gw, HOP_WAN: wan, HOP_DNS: dns}, bw
