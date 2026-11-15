"""
Deterministic tests for the RCA decision engine and chaos simulator.

Run:  cd backend && python -m pytest -q
"""

from __future__ import annotations

import random

import pytest

from app.config import settings
from app.engine.analyzer import Analyzer
from app.engine.prober import HOP_DNS, HOP_GATEWAY, HOP_WAN, NetworkInfo, ProbeSample
from app.engine.simulator import ChaosSimulator
from app.engine.system_monitor import BandwidthSnapshot

NET = NetworkInfo(interface="wlan0", local_ip="192.168.1.20", gateway_ip="192.168.1.1",
                  dns_server="192.168.1.1", probe_backend="test")


def bw(down: float = 1.0, up: float = 0.2, peak_down: float = 100.0, peak_up: float = 20.0) -> BandwidthSnapshot:
    return BandwidthSnapshot(down, up, peak_down, peak_up, "wlan0", 866.0, True, None)


def probes(gw: float | None, wan: float | None, dns: float | None) -> dict[str, ProbeSample]:
    return {
        HOP_GATEWAY: ProbeSample(HOP_GATEWAY, "192.168.1.1", gw, "test"),
        HOP_WAN: ProbeSample(HOP_WAN, "1.1.1.1", wan, "test"),
        HOP_DNS: ProbeSample(HOP_DNS, "192.168.1.1", dns, "udp53"),
    }


def run(n: int, gen, bandwidth=None, chaos: ChaosSimulator | None = None):
    analyzer = Analyzer(settings)
    rng = random.Random(7)
    frame = None
    for i in range(n):
        p = gen(rng, i)
        b = bandwidth or bw()
        if chaos:
            p, b = chaos.apply(p, b)
        frame = analyzer.ingest(p, b, NET, chaos.mode.value if chaos else "none")
    return frame


def healthy(rng, _i):
    return probes(rng.uniform(1, 4), rng.uniform(12, 20), rng.uniform(8, 20))


def test_baseline_then_healthy():
    first = run(1, healthy)
    assert first.diagnosis.title == "Collecting baseline telemetry"
    frame = run(20, healthy)
    assert frame.diagnosis.status == "HEALTHY"
    assert frame.diagnosis.root_cause_layer == "NONE"
    assert frame.diagnosis.confidence_score >= 80


def test_local_wifi():
    frame = run(20, lambda r, i: probes(r.uniform(50, 90), r.uniform(70, 110), r.uniform(60, 90)))
    assert frame.diagnosis.root_cause_layer == "LOCAL_WIFI"


def test_bufferbloat_requires_saturation():
    gen = lambda r, i: probes(r.uniform(50, 90), r.uniform(120, 180), r.uniform(40, 60))
    saturated = run(20, gen, bandwidth=bw(down=92, up=5))
    assert saturated.diagnosis.root_cause_layer == "BUFFERBLOAT"
    idle = run(20, gen, bandwidth=bw(down=2, up=0.5))
    assert idle.diagnosis.root_cause_layer == "LOCAL_WIFI"


def test_isp_wan():
    frame = run(20, lambda r, i: probes(r.uniform(1, 3), None if i % 4 == 0 else r.uniform(150, 220), 15))
    assert frame.diagnosis.root_cause_layer == "ISP_WAN"
    assert frame.diagnosis.status == "CRITICAL"  # 25 % loss


def test_dns_server():
    frame = run(20, lambda r, i: probes(2, 15, r.uniform(300, 600)))
    assert frame.diagnosis.root_cause_layer == "DNS_SERVER"


def test_total_outage():
    frame = run(25, lambda r, i: probes(None, None, None))
    assert frame.diagnosis.status == "CRITICAL"
    assert frame.diagnosis.title == "Network link down"


def test_gateway_that_filters_icmp_is_ignored():
    frame = run(25, lambda r, i: probes(None, r.uniform(12, 20), 15))
    assert frame.diagnosis.status == "HEALTHY"


def test_hop_math():
    analyzer = Analyzer(settings)
    for rtt in (10.0, 20.0, None, 30.0):
        analyzer.ingest(probes(rtt, 15, 15), bw(), NET)
    gw = analyzer.ingest(probes(40.0, 15, 15), bw(), NET).hops[HOP_GATEWAY]
    assert gw.loss_pct == pytest.approx(20.0)
    assert gw.avg_ms == pytest.approx(25.0)
    assert gw.jitter_ms == pytest.approx(10.0)  # |20-10|,|30-20|,|40-30|
    assert gw.samples == 5


@pytest.mark.parametrize(
    "mode,layer",
    [("wifi_lag", "LOCAL_WIFI"), ("isp_loss", "ISP_WAN"), ("dns_slowdown", "DNS_SERVER"), ("bufferbloat", "BUFFERBLOAT")],
)
def test_chaos_modes_are_diagnosed(mode, layer):
    chaos = ChaosSimulator(seed=42)
    chaos.set_mode(mode)
    frame = run(20, healthy, chaos=chaos)
    assert frame.diagnosis.root_cause_layer == layer
    assert frame.diagnosis.status in ("WARNING", "CRITICAL")
    assert frame.diagnosis.confidence_score >= 70


def test_single_dropped_probe_is_not_packet_loss():
    """Found on a real weak Wi-Fi link: 1 lost ping in 20 = 5 % used to raise LOCAL_WIFI."""
    frame = run(25, lambda r, i: probes(r.uniform(2, 6), r.uniform(9, 14), 12) if i != 22 else probes(None, 12, 12))
    assert frame.hops["gateway"].recent_lost == 1
    assert frame.hops["gateway"].status == "HEALTHY"
    assert frame.diagnosis.status == "HEALTHY"
    two = run(25, lambda r, i: probes(None if i in (20, 23) else r.uniform(2, 6), 12, 12))
    assert two.diagnosis.root_cause_layer == "LOCAL_WIFI"


def test_chaos_mode_switch_does_not_carry_over():
    """wifi_lag -> isp_loss back to back must be diagnosed as ISP, not leftover Wi-Fi loss."""
    analyzer = Analyzer(settings)
    rng = random.Random(1)
    chaos = ChaosSimulator(seed=9)
    for mode in ("wifi_lag", "isp_loss", "dns_slowdown", "bufferbloat"):
        analyzer.reset()                     # what POST /api/chaos/{mode} now does
        chaos.set_mode(mode)
        frame = None
        for i in range(9):                   # ~13 s, the time a demo user waits
            p, b = chaos.apply(healthy(rng, i), bw())
            frame = analyzer.ingest(p, b, NET, mode)
        expected = {"wifi_lag": "LOCAL_WIFI", "isp_loss": "ISP_WAN", "dns_slowdown": "DNS_SERVER",
                    "bufferbloat": "BUFFERBLOAT"}[mode]
        assert frame.diagnosis.root_cause_layer == expected, mode
