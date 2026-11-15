"""
Telemetry math + Root Cause Analysis decision engine.

Per hop we keep a ``deque(maxlen=60)`` of RTT samples (``None`` = lost probe)
and derive current / average / P95 / jitter / loss. Every tick the RCA engine
walks a deterministic decision tree and emits a structured ``Diagnosis``.

Decisions use a short "recent" view (median of the last 8 good RTTs, loss over
the last 20 probes) so the verdict reacts within seconds and is robust to a
single outlier, while the dashboard shows full-window statistics.

Decision order (first match wins):

  0. Link down          WAN + gateway unreachable                 -> LOCAL_WIFI   (CRITICAL)
  1. BUFFERBLOAT        (GW > 40 ms OR WAN > 100 ms) AND util > 70 %
  2. LOCAL_WIFI         (GW > 35 ms OR GW loss > 3 %) AND traffic < 15 Mbps
     LOCAL_WIFI (weak)  GW degraded under moderate, non-saturating load
  3. ISP_WAN            GW healthy (< 15 ms, 0 % loss) AND (WAN > 120 ms OR WAN loss > 3 %)
     ISP_WAN (weak)     WAN degraded while GW is merely "OK"
  4. DNS_SERVER         GW & WAN healthy AND (DNS > 150 ms OR DNS failing)
  5. HEALTHY
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..config import Settings
from .prober import HOP_DNS, HOP_GATEWAY, HOP_WAN, NetworkInfo, ProbeSample
from .system_monitor import BandwidthSnapshot
from .wifi_stats import WifiInfo

HEALTHY, WARNING, CRITICAL, UNKNOWN = "HEALTHY", "WARNING", "CRITICAL", "UNKNOWN"
LAYER_NONE, LOCAL_WIFI, ISP_WAN, DNS_SERVER, BUFFERBLOAT = (
    "NONE", "LOCAL_WIFI", "ISP_WAN", "DNS_SERVER", "BUFFERBLOAT",
)

HOP_LABELS = {
    HOP_GATEWAY: "Local Router / Gateway",
    HOP_WAN: "ISP / External WAN",
    HOP_DNS: "DNS Resolver",
}


def _r(value: float | None, nd: int = 2) -> float | None:
    return None if value is None or math.isnan(value) else round(float(value), nd)


# ------------------------------------------------------------------ stats
@dataclass
class HopStats:
    hop: str
    label: str
    target: str | None
    method: str
    current_ms: float | None
    avg_ms: float | None
    p95_ms: float | None
    min_ms: float | None
    max_ms: float | None
    jitter_ms: float | None
    loss_pct: float
    recent_ms: float | None
    recent_loss_pct: float
    samples: int
    last_ok: bool
    status: str
    simulated: bool = False
    # Adaptive baseline (robust z-score). Optional / backward compatible.
    baseline_median_ms: float | None = None
    baseline_sigma_ms: float | None = None
    baseline_samples: int = 0
    z_score: float | None = None
    anomaly: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaselineTracker:
    """Long rolling buffer of *good* RTTs with a robust (median/MAD) z-score.

    sigma = max(1.4826 * MAD, floor): 1.4826 makes the MAD a consistent
    estimator of the standard deviation for normal data, and the floor stops
    ultra-stable wired links (MAD ~ 0) from turning 0.3 ms of noise into z=10.
    """

    MAD_TO_SIGMA = 1.4826

    def __init__(self, maxlen: int, min_samples: int, sigma_floor_ms: float, admit_every: int = 10):
        self.samples: deque[float] = deque(maxlen=maxlen)
        self.min_samples = min_samples
        self.sigma_floor_ms = sigma_floor_ms
        self.admit_every = max(1, admit_every)
        self._held = 0

    @property
    def ready(self) -> bool:
        return len(self.samples) >= self.min_samples

    def reset(self) -> None:
        self.samples.clear()
        self._held = 0

    def stats(self) -> tuple[float | None, float | None]:
        if not self.samples:
            return None, None
        arr = np.fromiter(self.samples, dtype=float)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))
        return median, max(self.MAD_TO_SIGMA * mad, self.sigma_floor_ms)

    def zscore(self, value: float | None) -> float | None:
        median, sigma = self.stats()
        if value is None or median is None or sigma is None:
            return None
        return (value - median) / sigma

    def observe(self, rtt_ms: float | None, anomalous: bool = False) -> None:
        """Admit a good sample. While anomalous only every Nth sample enters,
        so a sustained fault cannot quickly redefine 'normal'."""
        if rtt_ms is None:
            return
        if anomalous:
            self._held += 1
            if self._held % self.admit_every:
                return
        self.samples.append(float(rtt_ms))


class HopWindow:
    def __init__(self, hop: str, maxlen: int):
        self.hop = hop
        self.samples: deque[float | None] = deque(maxlen=maxlen)
        self.target: str | None = None
        self.method: str = "-"
        self.simulated = False

    def add(self, sample: ProbeSample) -> None:
        self.samples.append(sample.rtt_ms)
        self.target = sample.target
        self.method = sample.method
        self.simulated = sample.simulated

    def clear(self) -> None:
        self.samples.clear()

    def compute(self, recent_rtt_n: int, recent_loss_n: int) -> dict[str, Any]:
        data = list(self.samples)
        ok = [s for s in data if s is not None]
        n = len(data)
        arr = np.asarray(ok, dtype=float)
        # Jitter: mean absolute difference between consecutive successful RTTs
        # (the RFC 3550 inter-arrival idea, without the 1/16 smoothing).
        jitter = float(np.mean(np.abs(np.diff(arr)))) if arr.size >= 2 else None
        recent_ok = [s for s in data[-recent_rtt_n * 2 :] if s is not None][-recent_rtt_n:]
        recent_window = data[-recent_loss_n:]
        return {
            "current_ms": data[-1] if data else None,
            "avg_ms": float(arr.mean()) if arr.size else None,
            "p95_ms": float(np.percentile(arr, 95)) if arr.size else None,
            "min_ms": float(arr.min()) if arr.size else None,
            "max_ms": float(arr.max()) if arr.size else None,
            "jitter_ms": jitter,
            "loss_pct": (n - len(ok)) / n * 100.0 if n else 0.0,
            "recent_ms": float(statistics.median(recent_ok)) if recent_ok else None,
            "recent_loss_pct": (
                sum(1 for s in recent_window if s is None) / len(recent_window) * 100.0
                if recent_window else 0.0
            ),
            "samples": n,
            "last_ok": bool(data) and data[-1] is not None,
        }


# --------------------------------------------------------------- diagnosis
@dataclass
class Diagnosis:
    status: str
    root_cause_layer: str
    confidence_score: int
    title: str
    explanation: str
    recommended_action: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetryFrame:
    tick: int
    timestamp: float
    chaos_mode: str
    network: NetworkInfo
    hops: dict[str, HopStats]
    bandwidth: BandwidthSnapshot
    diagnosis: Diagnosis
    wifi: WifiInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "timestamp": self.timestamp,
            "chaos_mode": self.chaos_mode,
            "network": self.network.to_dict(),
            "hops": {k: v.to_dict() for k, v in self.hops.items()},
            "bandwidth": self.bandwidth.to_dict(),
            "diagnosis": self.diagnosis.to_dict(),
            "wifi": self.wifi.to_dict() if self.wifi else None,
        }


def _excess(value: float | None, threshold: float) -> float:
    """How far past a threshold we are, 0..1 (1 == at least 2x the threshold)."""
    if value is None:
        return 1.0
    if value == math.inf:
        return 1.0
    return max(0.0, min(1.0, value / threshold - 1.0))


class Analyzer:
    def __init__(self, settings: Settings):
        self.s = settings
        self.windows = {h: HopWindow(h, settings.window_size) for h in (HOP_GATEWAY, HOP_WAN, HOP_DNS)}
        self.baselines = {
            h: BaselineTracker(
                settings.baseline_window_size,
                settings.baseline_min_samples,
                settings.baseline_sigma_floor_ms,
                settings.baseline_anomaly_admit_every,
            )
            for h in (HOP_GATEWAY, HOP_WAN, HOP_DNS)
        }
        self._network_key: tuple[str | None, str | None] | None = None
        self.tick = 0

    def reset(self, baselines: bool = False) -> None:
        for w in self.windows.values():
            w.clear()
        if baselines:
            for b in self.baselines.values():
                b.reset()

    def _anomaly_floor(self, hop: str) -> float:
        s = self.s
        return {
            HOP_GATEWAY: s.anomaly_floor_gateway_ms,
            HOP_WAN: s.anomaly_floor_wan_ms,
            HOP_DNS: s.anomaly_floor_dns_ms,
        }[hop]

    # --------------------------------------------------------- hop status
    def _hop_thresholds(self, hop: str) -> tuple[float, float]:
        s = self.s
        return {
            HOP_GATEWAY: (s.gateway_warn_ms, s.gateway_crit_ms),
            HOP_WAN: (s.wan_warn_ms, s.wan_crit_ms),
            HOP_DNS: (s.dns_warn_ms, s.dns_crit_ms),
        }[hop]

    def _hop_status(self, hop: str, m: dict[str, Any], has_target: bool, anomaly: bool = False) -> str:
        if not has_target or m["samples"] == 0:
            return UNKNOWN
        warn, crit = self._hop_thresholds(hop)
        rtt, loss = m["recent_ms"], m["recent_loss_pct"]
        if loss >= self.s.loss_crit_pct or rtt is None or rtt > crit:
            return CRITICAL
        if loss > self.s.loss_warn_pct or rtt > warn or anomaly:
            return WARNING
        return HEALTHY

    # -------------------------------------------------------------- ingest
    def ingest(
        self,
        probes: dict[str, ProbeSample],
        bandwidth: BandwidthSnapshot,
        network: NetworkInfo,
        chaos_mode: str = "none",
        wifi: WifiInfo | None = None,
    ) -> TelemetryFrame:
        self.tick += 1
        # A different gateway/resolver is a different network: start a new baseline.
        key = (network.gateway_ip, network.dns_server)
        if self._network_key is not None and key != self._network_key:
            for b in self.baselines.values():
                b.reset()
        self._network_key = key

        hops: dict[str, HopStats] = {}
        for hop, window in self.windows.items():
            sample = probes[hop]
            if sample.target is not None:
                window.add(sample)
            m = window.compute(self.s.recent_rtt_samples, self.s.recent_loss_samples)

            baseline = self.baselines[hop]
            b_median, b_sigma = baseline.stats()
            z = baseline.zscore(m["recent_ms"])
            anomaly = bool(
                baseline.ready
                and z is not None
                and z > self.s.zscore_threshold
                and m["recent_ms"] is not None
                and m["recent_ms"] > self._anomaly_floor(hop)
            )
            # Simulated (chaos) samples never pollute the learned baseline.
            if sample.target is not None and not sample.simulated:
                baseline.observe(sample.rtt_ms, anomalous=anomaly)
            hops[hop] = HopStats(
                hop=hop,
                label=HOP_LABELS[hop],
                target=sample.target,
                method=sample.method,
                current_ms=_r(m["current_ms"]),
                avg_ms=_r(m["avg_ms"]),
                p95_ms=_r(m["p95_ms"]),
                min_ms=_r(m["min_ms"]),
                max_ms=_r(m["max_ms"]),
                jitter_ms=_r(m["jitter_ms"]),
                loss_pct=round(m["loss_pct"], 1),
                recent_ms=_r(m["recent_ms"]),
                recent_loss_pct=round(m["recent_loss_pct"], 1),
                samples=m["samples"],
                last_ok=m["last_ok"],
                status=self._hop_status(hop, m, sample.target is not None, anomaly),
                simulated=sample.simulated,
                baseline_median_ms=_r(b_median),
                baseline_sigma_ms=_r(b_sigma),
                baseline_samples=len(baseline.samples),
                z_score=_r(z),
                anomaly=anomaly,
            )
        diagnosis = self.diagnose(hops, bandwidth, wifi)
        return TelemetryFrame(
            tick=self.tick,
            timestamp=time.time(),
            chaos_mode=chaos_mode,
            network=network,
            hops=hops,
            bandwidth=bandwidth,
            diagnosis=diagnosis,
            wifi=wifi,
        )

    # ------------------------------------------------------------- the RCA
    def diagnose(self, hops: dict[str, HopStats], bw: BandwidthSnapshot, wifi: WifiInfo | None = None) -> Diagnosis:
        s = self.s
        gw, wan, dns = hops[HOP_GATEWAY], hops[HOP_WAN], hops[HOP_DNS]

        def rtt(h: HopStats) -> float:
            return h.recent_ms if h.recent_ms is not None else math.inf

        gw_rtt, wan_rtt, dns_rtt = rtt(gw), rtt(wan), rtt(dns)
        gw_loss, wan_loss, dns_loss = gw.recent_loss_pct, wan.recent_loss_pct, dns.recent_loss_pct
        util = bw.utilization_pct
        traffic = bw.total_mbps
        top = (bw.top_process or {}).get("name")
        sample_bonus = 5.0 * min(1.0, min(gw.samples or wan.samples, wan.samples) / 20.0)

        evidence: dict[str, Any] = {
            "gateway_rtt_ms": _r(gw.recent_ms),
            "gateway_loss_pct": gw_loss,
            "wan_rtt_ms": _r(wan.recent_ms),
            "wan_loss_pct": wan_loss,
            "wan_jitter_ms": wan.jitter_ms,
            "dns_ms": _r(dns.recent_ms),
            "dns_loss_pct": dns_loss,
            "download_mbps": round(bw.download_mbps, 2),
            "upload_mbps": round(bw.upload_mbps, 2),
            "utilization_pct": util,
            "top_process": top,
            "gateway_z": gw.z_score,
            "wan_z": wan.z_score,
            "dns_z": dns.z_score,
            "gateway_baseline_ms": gw.baseline_median_ms,
            "wan_baseline_ms": wan.baseline_median_ms,
            "dns_baseline_ms": dns.baseline_median_ms,
        }
        if wifi is not None and wifi.is_wireless:
            evidence.update(
                rssi_dbm=wifi.rssi_dbm, wifi_band=wifi.band, wifi_channel=wifi.channel, wifi_ssid=wifi.ssid
            )

        def verdict(status, layer, conf, title, expl, action) -> Diagnosis:
            return Diagnosis(status, layer, int(max(5, min(99, round(conf)))), title, expl, action, evidence)

        if wan.samples < s.min_samples_for_verdict:
            return verdict(
                HEALTHY, LAYER_NONE, 20, "Collecting baseline telemetry",
                f"Gathering the first probes across gateway, WAN and DNS "
                f"({wan.samples}/{s.min_samples_for_verdict} samples). A verdict will appear in a few seconds.",
                "No action needed.",
            )

        # A gateway that never answers while the Internet works is just
        # filtering probes (common on ISP routers) -> treat as unknown.
        gw_known = gw.status != UNKNOWN and not (gw_loss >= 100 and wan_loss < 100)

        # ---- 0. total outage
        if wan_loss >= 100 and (not gw_known or gw_loss >= 100):
            local = "the router does not answer either" if gw_known else "no gateway responds"
            return verdict(
                CRITICAL, LOCAL_WIFI, 92 + sample_bonus, "Network link down",
                f"Every external probe in the last {s.recent_loss_samples} attempts failed and {local}. "
                "This device has no working path to the Internet.",
                "Check that Wi-Fi is connected (or the cable is seated), power-cycle the router/modem, "
                "and look at the router's WAN/Internet LED.",
            )

        # Hybrid triggering: static thresholds stay as hard guardrails, and the
        # adaptive z-score flags degradations that are abnormal *for this network*.
        gw_anom = gw_known and gw.anomaly
        wan_anom, dns_anom = wan.anomaly, dns.anomaly
        gw_static_bad = gw_known and (gw_rtt > s.gateway_warn_ms or gw_loss > s.loss_warn_pct)
        wan_static_bad = wan_rtt > s.wan_warn_ms or wan_loss > s.loss_warn_pct
        dns_static_bad = dns_rtt > s.dns_warn_ms or dns_loss > s.loss_warn_pct
        gw_bad = gw_static_bad or gw_anom
        wan_bad = wan_static_bad or wan_anom
        dns_bad = dns_static_bad or dns_anom
        gw_clean = (not gw_known) or (gw_rtt < s.gateway_healthy_ms and gw_loss == 0 and not gw_anom)
        wan_ok = not wan_bad and wan_rtt <= s.wan_bloat_ms

        def z_ex(h: HopStats) -> float:
            """0..1 strength of an anomaly (1 == z at twice the threshold)."""
            if not h.anomaly or h.z_score is None:
                return 0.0
            return max(0.0, min(1.0, (h.z_score - s.zscore_threshold) / s.zscore_threshold))

        def z_bonus(h: HopStats) -> float:
            return 4.0 + 6.0 * z_ex(h) if h.anomaly else 0.0

        def severity(*pairs: tuple[float, float], loss: float = 0.0) -> str:
            if loss >= s.loss_crit_pct or any(v > c for v, c in pairs):
                return CRITICAL
            return WARNING

        # ---- 1. bufferbloat
        bloat_latency = (gw_known and gw_rtt > s.gateway_bloat_ms) or wan_rtt > s.wan_bloat_ms or gw_anom or wan_anom
        if bloat_latency and util > s.bloat_utilization_pct:
            ex = max(
                _excess(gw_rtt, s.gateway_bloat_ms) if gw_known else 0.0,
                _excess(wan_rtt, s.wan_bloat_ms),
                z_ex(gw), z_ex(wan),
            )
            util_factor = (util - s.bloat_utilization_pct) / (100 - s.bloat_utilization_pct)
            conf = (58 + 18 * ex + 14 * util_factor + sample_bonus + (4 if dns_rtt < s.dns_warn_ms else 0)
                    + max(z_bonus(gw), z_bonus(wan)) / 2)
            hog = f" The heaviest network user right now is '{top}'." if top else ""
            direction = "upload" if bw.upload_mbps / bw.peak_upload_mbps > bw.download_mbps / bw.peak_download_mbps else "download"
            return verdict(
                severity((gw_rtt, s.gateway_crit_ms), (wan_rtt, s.wan_crit_ms), loss=max(gw_loss, wan_loss)),
                BUFFERBLOAT, conf, "Local bandwidth saturation (bufferbloat)",
                f"Your link is running at {util:.0f}% of its observed peak ({bw.download_mbps:.1f} Mbps down / "
                f"{bw.upload_mbps:.1f} Mbps up) and latency has inflated to {self._fmt(gw_rtt)} at the gateway and "
                f"{self._fmt(wan_rtt)} to the Internet. Packets are queuing in an oversized router buffer, so "
                f"interactive traffic (calls, games, browsing) waits behind the bulk {direction}.{hog}"
                f"{self._baseline_note(wan if wan.anomaly else gw)}",
                "Pause or rate-limit the bulk transfer, and enable Smart Queue Management (SQM: fq_codel or CAKE) "
                "on your router, setting its shaper to ~90% of your plan's speed. QoS for real-time apps also helps.",
            )

        # ---- 2. local Wi-Fi / LAN
        if gw_bad:
            ex = max(
                _excess(gw_rtt, s.gateway_warn_ms) if gw_static_bad else 0.0,
                _excess(gw_loss, s.loss_warn_pct) if gw_loss > 0 else 0,
                z_ex(gw),
            )
            phys_text, phys_action, phys_bonus = self._wifi_evidence(wifi)
            downstream_follows = wan_rtt >= gw_rtt  # WAN latency inherits the local delay
            low_traffic = traffic < s.local_bw_limit_mbps
            conf = (
                55 + 25 * ex + sample_bonus
                + (8 if downstream_follows else 0)
                + (6 if gw.jitter_ms and gw.jitter_ms > 10 else 0)
                - (0 if low_traffic else 18)
                + z_bonus(gw) + phys_bonus
            )
            load_note = (
                f"Local traffic is low ({traffic:.1f} Mbps), so this is not caused by your own downloads."
                if low_traffic
                else f"Local traffic is moderate ({traffic:.1f} Mbps) but not saturating the link, "
                     "so radio conditions remain the most likely cause."
            )
            return verdict(
                severity((gw_rtt, s.gateway_crit_ms), loss=gw_loss), LOCAL_WIFI, conf,
                "Wi-Fi signal degradation or router congestion",
                f"The very first hop is unhealthy: the gateway ({gw.target}) answers in {self._fmt(gw_rtt)} with "
                f"{gw_loss:.0f}% loss and {self._fmt(gw.jitter_ms)} jitter, where a healthy LAN is under "
                f"{s.gateway_healthy_ms:.0f} ms. Every packet to the Internet must cross this hop first. {load_note}"
                f"{self._baseline_note(gw)}{phys_text}",
                f"{phys_action}Move closer to the router or remove obstructions, switch to the 5 GHz / 6 GHz band, change to a "
                "less congested Wi-Fi channel, and reboot the router. If it persists, test with an Ethernet cable "
                "to confirm the wireless link is the culprit.",
            )

        # ---- 3. ISP / WAN
        if wan_bad:
            ex = max(
                _excess(wan_rtt, s.wan_warn_ms) if wan_static_bad else 0.0,
                _excess(wan_loss, s.loss_warn_pct) if wan_loss > 0 else 0,
                z_ex(wan),
            )
            conf = 58 + 22 * ex + sample_bonus + (12 if gw_clean else 0) + z_bonus(wan)
            gw_note = (
                f"Your local network is clean (gateway {self._fmt(gw_rtt)}, 0% loss), so the problem is beyond your router."
                if gw_clean and gw_known
                else "The gateway could not be measured directly, but local traffic is light."
                if not gw_known
                else f"The gateway is acceptable ({self._fmt(gw_rtt)}), and the WAN degradation is far larger."
            )
            return verdict(
                severity((wan_rtt, s.wan_crit_ms), loss=wan_loss), ISP_WAN, conf,
                "ISP backbone congestion or upstream routing issue",
                f"Latency to {wan.target} is {self._fmt(wan_rtt)} with {wan_loss:.0f}% packet loss and "
                f"{self._fmt(wan.jitter_ms)} jitter. {gw_note}{self._baseline_note(wan)}",
                "Power-cycle the modem/ONT, then check your ISP's status page. If degradation persists, export the "
                "ISP Evidence Report (CSV) below and open a ticket with your provider — it timestamps the loss and "
                "proves your local network was healthy.",
            )

        # ---- 4. DNS
        if dns_bad and wan_ok:
            failing = dns_loss > s.loss_warn_pct
            ex = max(
                _excess(dns_rtt, s.dns_warn_ms) if dns_static_bad else 0.0,
                _excess(dns_loss, s.loss_warn_pct) if failing else 0,
                z_ex(dns),
            )
            conf = 62 + 22 * ex + sample_bonus + (8 if gw_clean else 0) + z_bonus(dns)
            what = (
                f"{dns_loss:.0f}% of DNS queries are timing out"
                if failing else f"name resolution takes {self._fmt(dns_rtt)}"
            )
            return verdict(
                severity((dns_rtt, s.dns_crit_ms), loss=dns_loss), DNS_SERVER, conf,
                "Slow or unresponsive DNS resolver",
                f"Connectivity itself is fine (gateway {self._fmt(gw_rtt)}, WAN {self._fmt(wan_rtt)}), but {what} "
                f"against resolver {dns.target}. Every new website or app connection waits on DNS before it can "
                f"start, which feels like a slow Internet even though bandwidth is fine.{self._baseline_note(dns)}",
                "Switch your DNS to a fast public resolver such as 1.1.1.1 / 1.0.0.1 (Cloudflare) or 8.8.8.8 / "
                "8.8.4.4 (Google), on this device or on the router's DHCP settings so all devices benefit.",
            )

        # ---- 5. healthy
        margins = [1 - min(wan_rtt / s.wan_warn_ms, 1), 1 - min(dns_rtt / s.dns_warn_ms, 1)]
        if gw_known:
            margins.append(1 - min(gw_rtt / s.gateway_warn_ms, 1))
        conf = 70 + 24 * min(margins) + sample_bonus
        return verdict(
            HEALTHY, LAYER_NONE, conf, "All network layers healthy",
            f"Gateway {self._fmt(gw_rtt) if gw_known else 'n/a'}, Internet {self._fmt(wan_rtt)}, "
            f"DNS {self._fmt(dns_rtt)}; packet loss {max(gw_loss if gw_known else 0, wan_loss):.0f}%. "
            "Every hop is inside its normal operating envelope.",
            "No action needed. Use the Chaos Simulation bar to see how WiFi-Doctor diagnoses real faults.",
        )

    def _baseline_note(self, h: HopStats) -> str:
        if not h.anomaly or h.baseline_median_ms is None or h.z_score is None:
            return ""
        return (
            f" That is abnormal for this network: the learned baseline for this hop is "
            f"{self._fmt(h.baseline_median_ms)} (robust z-score {h.z_score:+.1f} over "
            f"{h.baseline_samples} samples)."
        )

    def _wifi_evidence(self, wifi: WifiInfo | None) -> tuple[str, str, float]:
        """Physical-layer corroboration for a LOCAL_WIFI verdict."""
        if wifi is None or not wifi.is_wireless:
            return "", "", 0.0
        weak = wifi.rssi_dbm is not None and wifi.rssi_dbm < self.s.wifi_weak_rssi_dbm
        on_24 = wifi.band == "2.4 GHz"
        if not (weak or on_24):
            return "", "", 0.0
        signal = f"{wifi.rssi_dbm} dBm" if wifi.rssi_dbm is not None else "unknown strength"
        where = f" on {wifi.band}" if wifi.band else ""
        if wifi.channel is not None:
            where += f" channel {wifi.channel}"
        label = "Weak Wi-Fi signal" if weak else "Wi-Fi signal"
        text = f" Physical layer: {label.lower()} ({signal}){where}."
        if weak and on_24:
            action = f"{label} ({signal}){where}: switch to 5 GHz or move closer to the router. "
        elif weak:
            action = f"{label} ({signal}){where}: move closer to the router or remove obstructions. "
        else:
            action = f"You are on the congested 2.4 GHz band{(' channel ' + str(wifi.channel)) if wifi.channel else ''}: switch to 5 GHz. "
        return text, action, (8.0 if weak else 0.0) + (3.0 if on_24 else 0.0)

    @staticmethod
    def _fmt(ms: float | None) -> str:
        if ms is None or ms == math.inf:
            return "no reply"
        return f"{ms:.0f} ms" if ms >= 10 else f"{ms:.1f} ms"
