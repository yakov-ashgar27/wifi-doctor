"""
Tests for the v1.1 features:
  1. adaptive baselines (robust MAD z-score + hybrid RCA triggering)
  2. physical Wi-Fi telemetry parsers (netsh / iw / proc / macOS) + RCA enrichment
  3. bufferbloat stress test grading, runner and the HTTP 409 concurrency guard

Run:  cd backend && python -m pytest -q
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from types import SimpleNamespace

import pytest

from app.config import settings
from app.engine.analyzer import Analyzer, BaselineTracker
from app.engine.prober import HOP_DNS, HOP_GATEWAY, HOP_WAN, NetworkInfo, ProbeSample
from app.engine.simulator import ChaosSimulator
from app.engine.stress_test import (
    StressTestBusy,
    StressTestRunner,
    TransferCounters,
    _Sample,
    compute_result,
    grade_for_inflation,
    needs_sqm,
)
from app.engine.system_monitor import BandwidthSnapshot
from app.engine.wifi_stats import (
    WifiInfo,
    band_from_frequency,
    channel_from_frequency,
    parse_airport,
    parse_iw_link,
    parse_netsh,
    parse_proc_wireless,
    parse_system_profiler,
    pct_to_dbm,
)

NET = NetworkInfo(interface="wlan0", local_ip="192.168.1.20", gateway_ip="192.168.1.1",
                  dns_server="192.168.1.1", probe_backend="test")


def bw(down: float = 1.0, up: float = 0.2) -> BandwidthSnapshot:
    return BandwidthSnapshot(down, up, 100.0, 20.0, "wlan0", 866.0, True, None)


def probes(gw, wan, dns) -> dict[str, ProbeSample]:
    return {
        HOP_GATEWAY: ProbeSample(HOP_GATEWAY, "192.168.1.1", gw, "test"),
        HOP_WAN: ProbeSample(HOP_WAN, "1.1.1.1", wan, "test"),
        HOP_DNS: ProbeSample(HOP_DNS, "192.168.1.1", dns, "udp53"),
    }


# =================================================================== 1. z-score
class TestBaselineTracker:
    def test_mad_sigma_and_zscore(self):
        b = BaselineTracker(maxlen=400, min_samples=3, sigma_floor_ms=0.1)
        for v in (10, 11, 12, 13, 14):
            b.observe(v)
        median, sigma = b.stats()
        assert median == 12
        assert sigma == pytest.approx(1.4826 * 1.0)          # MAD = 1
        assert b.zscore(20) == pytest.approx(8 / 1.4826)     # (20-12)/sigma

    def test_mad_is_robust_to_outliers(self):
        b = BaselineTracker(maxlen=400, min_samples=3, sigma_floor_ms=0.1)
        for v in (10, 10, 11, 11, 12, 12, 900):                # one huge spike
            b.observe(v)
        median, sigma = b.stats()
        assert median == 11
        assert sigma == pytest.approx(1.4826)                 # std would be ~330

    def test_sigma_floor_on_ultra_stable_link(self):
        b = BaselineTracker(maxlen=400, min_samples=3, sigma_floor_ms=1.0)
        for _ in range(50):
            b.observe(5.0)
        assert b.stats() == (5.0, 1.0)                        # MAD 0 -> floor
        assert b.zscore(6.0) == pytest.approx(1.0)            # no division by zero

    def test_ready_and_rolling_window(self):
        b = BaselineTracker(maxlen=10, min_samples=5, sigma_floor_ms=1.0)
        for i in range(4):
            b.observe(i)
        assert not b.ready
        for i in range(20):
            b.observe(100 + i)
        assert b.ready and len(b.samples) == 10
        b.observe(None)
        assert len(b.samples) == 10

    def test_anomalous_samples_are_throttled(self):
        b = BaselineTracker(maxlen=400, min_samples=1, sigma_floor_ms=1.0, admit_every=10)
        for _ in range(30):
            b.observe(500.0, anomalous=True)
        assert len(b.samples) == 3                            # every 10th only


def _feed(analyzer: Analyzer, n: int, gen, bandwidth=None):
    rng = random.Random(3)
    frame = None
    for i in range(n):
        frame = analyzer.ingest(gen(rng, i), bandwidth or bw(), NET)
    return frame


def _stable(rng, _i):
    return probes(rng.uniform(1.5, 2.5), rng.uniform(11, 13), rng.uniform(8, 10))


class TestHybridTriggering:
    def test_baseline_fields_surface_in_frame(self):
        frame = _feed(Analyzer(settings), 80, _stable)
        wan = frame.hops[HOP_WAN]
        assert wan.baseline_samples >= settings.baseline_min_samples
        assert 11 <= wan.baseline_median_ms <= 13
        assert wan.z_score is not None and abs(wan.z_score) < 3.5
        assert not wan.anomaly
        d = frame.to_dict()["hops"]["wan"]
        assert {"baseline_median_ms", "z_score", "anomaly", "baseline_samples"} <= d.keys()

    def test_adaptive_trigger_catches_what_static_misses(self):
        """WAN goes 12 ms -> 60 ms: far below the 120 ms static threshold, but 30+ sigma
        above this network's normal. Only the adaptive path can see it."""
        a = Analyzer(settings)
        _feed(a, 100, _stable)
        frame = _feed(a, 12, lambda r, i: probes(r.uniform(1.5, 2.5), r.uniform(58, 62), r.uniform(8, 10)))
        wan = frame.hops[HOP_WAN]
        assert wan.anomaly and wan.z_score > 3.5
        assert wan.recent_ms < settings.wan_warn_ms           # static would say "healthy"
        assert wan.status == "WARNING"
        assert frame.diagnosis.root_cause_layer == "ISP_WAN"
        assert frame.diagnosis.status == "WARNING"
        assert "baseline" in frame.diagnosis.explanation

    def test_no_anomaly_without_enough_baseline(self):
        a = Analyzer(settings)
        _feed(a, 30, _stable)                                 # < 60 samples
        frame = _feed(a, 12, lambda r, i: probes(2, r.uniform(58, 62), 9))
        assert not frame.hops[HOP_WAN].anomaly
        assert frame.diagnosis.status == "HEALTHY"

    def test_noise_floor_suppresses_tiny_absolute_changes(self):
        """3 ms -> 12 ms on the gateway is a huge z-score but below the 15 ms floor."""
        a = Analyzer(settings)
        _feed(a, 100, lambda r, i: probes(r.uniform(2.9, 3.1), 12, 9))
        frame = _feed(a, 12, lambda r, i: probes(12.0, 12, 9))
        gw = frame.hops[HOP_GATEWAY]
        assert gw.z_score > 3.5 and not gw.anomaly
        assert frame.diagnosis.status == "HEALTHY"

    def test_static_guardrail_still_wins_without_baseline(self):
        frame = _feed(Analyzer(settings), 20, lambda r, i: probes(2, r.uniform(150, 200), 9))
        assert frame.diagnosis.root_cause_layer == "ISP_WAN"
        assert frame.hops[HOP_WAN].anomaly is False

    def test_zscore_raises_confidence(self):
        a = Analyzer(settings)
        _feed(a, 100, _stable)
        anomalous = _feed(a, 12, lambda r, i: probes(2, r.uniform(150, 160), 9))
        cold = _feed(Analyzer(settings), 12, lambda r, i: probes(2, r.uniform(150, 160), 9))
        assert anomalous.diagnosis.confidence_score > cold.diagnosis.confidence_score

    def test_simulated_samples_do_not_pollute_baseline(self):
        a = Analyzer(settings)
        _feed(a, 70, _stable)
        before = a.baselines[HOP_WAN].stats()[0]
        chaos = ChaosSimulator(seed=1)
        chaos.set_mode("isp_loss")
        for i in range(40):
            p, b = chaos.apply(_stable(random.Random(i), i), bw())
            a.ingest(p, b, NET, "isp_loss")
        assert a.baselines[HOP_WAN].stats()[0] == pytest.approx(before, abs=0.5)

    def test_network_change_resets_baseline(self):
        a = Analyzer(settings)
        _feed(a, 70, _stable)
        other = NetworkInfo(gateway_ip="10.0.0.1", dns_server="10.0.0.1", probe_backend="test")
        frame = a.ingest(_stable(random.Random(0), 0), bw(), other)
        assert frame.hops[HOP_WAN].baseline_samples <= 1


# ================================================================ 2. Wi-Fi PHY
NETSH_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 4c1b2a3d-1111-2222-3333-444455556666
    Physical address       : a0:b1:c2:d3:e4:f5
    Interface type         : Primary
    State                  : connected
    SSID                   : HomeNet
    AP BSSID               : 11:22:33:44:55:66
    Band                   : 5 GHz
    Channel                : 36
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Connection mode        : Auto Connect
    Receive rate (Mbps)    : 1201
    Transmit rate (Mbps)   : 960.7
    Signal                 : 92%
    Rssi                   : -48
    Profile                : HomeNet

    Hosted network status  : Not available
"""

NETSH_DE_NO_RSSI = """
Es ist 1 Schnittstelle auf dem System vorhanden:

    Name                   : WLAN
    Beschreibung           : Realtek RTL8821CE
    GUID                   : 0f0f0f0f-aaaa-bbbb-cccc-dddddddddddd
    Physische Adresse      : 00:11:22:33:44:55
    Status                 : Verbunden
    SSID                   : Heimnetz
    Funktyp                : 802.11n
    Kanal                  : 6
    Empfangsrate (MBit/s)  : 144.4
    Übertragungsrate (MBit/s) : 130
    Signal                 : 40%
    Profil                 : Heimnetz
"""

NETSH_DISCONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    GUID                   : 4c1b2a3d-1111-2222-3333-444455556666
    State                  : disconnected
    Radio status           : Hardware On
"""

NETSH_LOCATION = (
    "Network shell commands need location permission to access WLAN information. "
    "Turn on Location services on the Location page in Privacy & security settings."
)

IW_LINK = """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: CoffeeShop
\tfreq: 2437.0
\tRX: 123456 bytes (789 packets)
\tTX: 23456 bytes (123 packets)
\tsignal: -78 dBm
\trx bitrate: 72.2 MBit/s MCS 7 short GI
\ttx bitrate: 65.0 MBit/s MCS 6
\tbss flags: short-slot-time
\tdtim period: 1
\tbeacon int: 100
"""

PROC_WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlp2s0: 0000   54.  -56.  -256        0      0      0      0     11        0
"""

AIRPORT = """     agrCtlRSSI: -61
     agrExtRSSI: 0
          state: running
        op mode: station
     lastTxRate: 866
        maxRate: 1300
           SSID: MacNet
        channel: 149,80
"""

SYSTEM_PROFILER = """Wi-Fi:

      Software Versions:
          CoreWLAN: 16.0 (1657)
      Interfaces:
        en0:
          Card Type: Wi-Fi  (0x14E4, 0x4387)
          Status: Connected
          Current Network Information:
            OfficeNet:
              PHY Mode: 802.11ax
              Channel: 149 (5GHz, 80MHz)
              Country Code: IL
              Signal / Noise: -55 dBm / -92 dBm
              Transmit Rate: 864
          Other Local Wi-Fi Networks:
            Neighbor:
              Channel: 6 (2GHz, 20MHz)
"""


class TestWifiParsers:
    def test_netsh_english_full(self):
        w = parse_netsh(NETSH_EN)
        assert w.is_wireless and w.source == "netsh"
        assert (w.interface, w.ssid, w.band, w.channel) == ("Wi-Fi", "HomeNet", "5 GHz", 36)
        assert w.rssi_dbm == -48 and not w.rssi_estimated
        assert w.signal_pct == 92
        assert w.radio_type == "802.11ax"
        assert (w.rx_rate_mbps, w.tx_rate_mbps) == (1201, 960.7)
        assert w.signal_quality == "good"

    def test_netsh_localized_without_rssi_line(self):
        w = parse_netsh(NETSH_DE_NO_RSSI)
        assert w.is_wireless and w.ssid == "Heimnetz"
        assert w.signal_pct == 40
        assert w.rssi_dbm == pct_to_dbm(40) == -80 and w.rssi_estimated
        assert w.channel == 6 and w.band == "2.4 GHz"        # band inferred from channel
        assert (w.rx_rate_mbps, w.tx_rate_mbps) == (144.4, 130)
        assert w.signal_quality == "weak"

    def test_netsh_disconnected_and_no_adapter(self):
        assert parse_netsh(NETSH_DISCONNECTED).is_wireless is False
        none = parse_netsh("There is no wireless interface on the system.")
        assert none.is_wireless is False and none.rssi_dbm is None
        assert parse_netsh("").is_wireless is False

    def test_netsh_location_permission(self):
        w = parse_netsh(NETSH_LOCATION)
        assert w.error == "location-permission-required" and w.rssi_dbm is None

    def test_iw_link(self):
        w = parse_iw_link(IW_LINK, "wlan0")
        assert w.is_wireless and w.ssid == "CoffeeShop"
        assert (w.rssi_dbm, w.frequency_mhz, w.band, w.channel) == (-78, 2437, "2.4 GHz", 6)
        assert (w.rx_rate_mbps, w.tx_rate_mbps) == (72.2, 65.0)
        assert w.radio_type == "802.11n"

    def test_iw_not_connected_and_ethernet(self):
        assert parse_iw_link("Not connected.", "wlan0").is_wireless is False
        assert parse_iw_link("", "eth0").is_wireless is False

    def test_proc_wireless(self):
        w = parse_proc_wireless(PROC_WIRELESS, "wlp2s0")
        assert w.is_wireless and w.rssi_dbm == -56
        assert parse_proc_wireless(PROC_WIRELESS, "eth0").is_wireless is False
        assert parse_proc_wireless("", None).is_wireless is False

    def test_macos_parsers(self):
        a = parse_airport(AIRPORT)
        assert (a.ssid, a.rssi_dbm, a.channel, a.band, a.tx_rate_mbps) == ("MacNet", -61, 149, "5 GHz", 866)
        sp = parse_system_profiler(SYSTEM_PROFILER)
        assert (sp.ssid, sp.rssi_dbm, sp.channel, sp.band, sp.radio_type) == ("OfficeNet", -55, 149, "5 GHz", "802.11ax")
        assert parse_airport("AirPort: Off").is_wireless is False

    def test_frequency_helpers(self):
        assert (band_from_frequency(2412), channel_from_frequency(2412)) == ("2.4 GHz", 1)
        assert (band_from_frequency(5180), channel_from_frequency(5180)) == ("5 GHz", 36)
        assert (band_from_frequency(5975), channel_from_frequency(5975)) == ("6 GHz", 5)
        assert channel_from_frequency(2484) == 14 and band_from_frequency(None) is None

    def test_wifi_info_serializes(self):
        d = parse_netsh(NETSH_EN).to_dict()
        assert d["signal_quality"] == "good" and "extra" not in d


class TestWifiRca:
    def _local_wifi_frame(self, wifi: WifiInfo | None):
        a = Analyzer(settings)
        rng = random.Random(5)
        frame = None
        for i in range(20):
            frame = a.ingest(probes(rng.uniform(60, 90), rng.uniform(80, 110), 40), bw(), NET, wifi=wifi)
        return frame

    def test_weak_24ghz_signal_enriches_diagnosis(self):
        weak = parse_iw_link(IW_LINK, "wlan0")
        frame = self._local_wifi_frame(weak)
        d = frame.diagnosis
        assert d.root_cause_layer == "LOCAL_WIFI"
        assert "-78 dBm" in d.explanation and "2.4 GHz channel 6" in d.explanation
        assert d.recommended_action.startswith("Weak Wi-Fi signal (-78 dBm) on 2.4 GHz channel 6")
        assert "switch to 5 GHz" in d.recommended_action
        assert d.evidence["rssi_dbm"] == -78
        plain = self._local_wifi_frame(None)
        assert d.confidence_score >= plain.diagnosis.confidence_score
        assert frame.to_dict()["wifi"]["band"] == "2.4 GHz"

    def test_strong_5ghz_signal_adds_nothing(self):
        frame = self._local_wifi_frame(parse_netsh(NETSH_EN))
        assert "Physical layer" not in frame.diagnosis.explanation

    def test_ethernet_is_clean(self):
        frame = self._local_wifi_frame(WifiInfo(is_wireless=False))
        assert frame.diagnosis.root_cause_layer == "LOCAL_WIFI"
        assert frame.to_dict()["wifi"]["is_wireless"] is False

    def test_chaos_wifi_lag_simulates_weak_radio(self):
        chaos = ChaosSimulator(seed=2)
        assert chaos.apply_wifi(None) is None
        chaos.set_mode("wifi_lag")
        w = chaos.apply_wifi(parse_netsh(NETSH_EN))
        assert w.simulated and w.band == "2.4 GHz" and w.rssi_dbm < -72


# ======================================================== 3. bufferbloat test
@pytest.mark.parametrize(
    "inflation,grade",
    [(0, "A+"), (4.9, "A+"), (5, "A"), (29.9, "A"), (30, "B"), (59, "B"), (60, "C"),
     (199, "C"), (200, "D"), (399, "D"), (400, "F"), (2000, "F"), (None, "F")],
)
def test_grade_scale(inflation, grade):
    assert grade_for_inflation(inflation) == grade


def test_needs_sqm():
    assert [g for g in ("A+", "A", "B", "C", "D", "F") if needs_sqm(g)] == ["C", "D", "F"]


def test_compute_result_inflation_and_multiplier():
    unloaded = [_Sample(2, 10), _Sample(2, 12), _Sample(3, 11), _Sample(2, 11), _Sample(2, 11)]
    loaded = [_Sample(40, 150), _Sample(55, 170), _Sample(50, 160), _Sample(None, None), _Sample(45, 165)]
    r = compute_result(unloaded, loaded, 92.4, 18.1, 50_000_000, 9_000_000, "https://x")
    assert r.unloaded_wan_ms == 11.0
    assert r.loaded_wan_median_ms == 162.5
    assert r.inflation_ms == 151.5 and r.grade == "C"
    assert r.inflation_multiplier == pytest.approx(14.77, abs=0.01)
    assert r.lost_samples == 1 and r.loaded_samples == 5
    assert r.recommendation and "SQM" in r.recommendation
    assert r.reference_hop == "wan"


def test_compute_result_good_link_and_edge_cases():
    good = compute_result([_Sample(2, 10)] * 5, [_Sample(3, 25)] * 4, 300, 40, 1, 1, None)
    assert good.grade == "A" and good.recommendation is None
    lost = compute_result([_Sample(2, 10)] * 5, [_Sample(None, None)] * 4, 0, 0, 1, 1, None)
    assert lost.grade == "F" and lost.inflation_ms is None
    assert compute_result([], [_Sample(2, 10)], 0, 0, 1, 1, None).grade == "?"
    assert compute_result([_Sample(2, 10)], [], 0, 0, 1, 1, None).grade == "?"
    wan_blocked = compute_result([_Sample(2, None)] * 5, [_Sample(30, None)] * 4, 0, 0, 1, 1, None)
    assert wan_blocked.reference_hop == "gateway" and wan_blocked.grade == "A"


def _fake_transfer(rate_bytes: int = 200_000):
    def transfer(urls, direction, stop: threading.Event, counters: TransferCounters, timeout):
        while not stop.wait(0.05):
            counters.add(direction, rate_bytes, urls[0] if urls else None)
    return transfer


def _frames_with(gw, wan):
    return lambda: [{"hops": {"gateway": {"current_ms": gw}, "wan": {"current_ms": wan}}}] * 5


def test_runner_completes_and_grades():
    async def scenario():
        events = []

        async def broadcast(msg):
            events.append(msg)

        runner = StressTestRunner(settings, _frames_with(2.0, 12.0), broadcast, _fake_transfer(),
                                  duration_s=0.6, timeout_s=3, warmup_s=0.0)
        await runner.start()
        for _ in range(4):                                     # telemetry ticks under load
            runner.on_frame({"hops": {"gateway": {"current_ms": 30.0}, "wan": {"current_ms": 90.0}}})
            await asyncio.sleep(0.05)
        await runner.wait()
        return runner, events

    runner, events = asyncio.run(scenario())
    st = runner.status()
    assert st["state"] == "completed" and st["progress"] == 1.0
    assert st["result"]["grade"] == "C"                       # +78 ms
    assert st["result"]["inflation_ms"] == 78.0
    assert st["result"]["peak_download_mbps"] > 0
    assert any(e["status"]["state"] == "running" for e in events)
    assert events[-1]["type"] == "stress_test" and events[-1]["status"]["state"] == "completed"


def test_runner_rejects_concurrent_runs_and_chaos():
    async def scenario():
        runner = StressTestRunner(settings, _frames_with(2, 12), None, _fake_transfer(),
                                  duration_s=0.5, timeout_s=3)
        with pytest.raises(StressTestBusy):
            await runner.start(chaos_active=True)
        await runner.start()
        with pytest.raises(StressTestBusy):
            await runner.start()
        await runner.wait()
        await runner.start()                                  # allowed again once finished
        await runner.wait()
        return runner

    assert asyncio.run(scenario()).state == "completed"


def test_runner_fails_cleanly_when_offline():
    def dead(urls, direction, stop, counters, timeout):
        counters.error("down: URLError")

    async def scenario():
        runner = StressTestRunner(settings, _frames_with(2, 12), None, dead, duration_s=0.3, timeout_s=2)
        await runner.start()
        await runner.wait()
        return runner

    r = asyncio.run(scenario())
    assert r.state == "failed" and "speed-test endpoint" in r.error and r.result is None


def test_runner_strict_timeout_aborts():
    async def scenario():
        runner = StressTestRunner(settings, _frames_with(2, 12), None, _fake_transfer(),
                                  duration_s=5.0, timeout_s=0.4)
        t0 = time.monotonic()
        await runner.start()
        await runner.wait()
        return runner, time.monotonic() - t0

    runner, elapsed = asyncio.run(scenario())
    assert runner.state == "aborted" and "timeout" in runner.error
    assert elapsed < 2.5


# ------------------------------------------------------------- HTTP layer
def test_api_returns_409_when_already_running():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import stress

    runner = StressTestRunner(settings, _frames_with(2, 12), None, _fake_transfer(), duration_s=1.5, timeout_s=4)
    app = FastAPI()
    app.include_router(stress.router)
    app.state.service = SimpleNamespace(stress=runner, simulator=ChaosSimulator())

    with TestClient(app) as client:
        first = client.post("/api/stress-test/start")
        assert first.status_code == 202 and first.json()["state"] == "running"
        second = client.post("/api/stress-test/start")
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]
        status = client.get("/api/stress-test/status").json()
        assert status["state"] == "running" and 0 <= status["progress"] <= 1


def test_api_returns_409_while_chaos_active():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import stress

    sim = ChaosSimulator()
    sim.set_mode("isp_loss")
    app = FastAPI()
    app.include_router(stress.router)
    app.state.service = SimpleNamespace(
        stress=StressTestRunner(settings, _frames_with(2, 12), None, _fake_transfer(), duration_s=0.2),
        simulator=sim,
    )
    with TestClient(app) as client:
        r = client.post("/api/stress-test/start")
        assert r.status_code == 409 and "chaos" in r.json()["detail"].lower()
