# Changelog

## v1.1.1 (2026-11-15): field-hardening

Every fix below came from running WiFi-Doctor on a real Windows 11 laptop. Each has a regression test.

- **RCA:** a single dropped probe no longer counts as packet loss (minimum 2 lost probes per window).
- **RCA:** rolling windows reset when the chaos mode changes, so back-to-back simulations are diagnosed independently.
- **Stress test:** self-induced load is excluded from incidents and from the learned baseline, and the windows are cleared when the test ends.
- **Stress test:** transfer errors are logged and returned as `diagnostics` when no speed-test endpoint is reachable.
- **Diagnosis:** Wi-Fi remediation no longer repeats the same advice twice. "Wi-Fi" casing is fixed in the physical-layer text.
- **UI:** the chaos panel no longer stretches to the height of the bufferbloat card.
- **Windows launcher** (`scripts/run_local.bat`):
  - repairs a broken session PATH and installs missing tools via winget
  - stops only its own previous instance, never other apps
  - picks free ports when 8000 or 5173 are taken
  - keeps the backend tied to the console window

## v1.1.0 (2026-11-15)

- **Adaptive anomaly baselines:** a per-hop rolling median/MAD baseline (400 samples) with a robust z-score, combined with the static thresholds (hybrid triggering).
- **Physical Wi-Fi telemetry:** RSSI, band, channel, SSID and PHY rates from `netsh` (locale-tolerant), `iw`, `/proc/net/wireless`, `airport` and `system_profiler`. The data is used as evidence in LOCAL_WIFI diagnoses and shown as a topology badge.
- **Active bufferbloat test:** an 8-second load test that compares loaded and unloaded latency and grades the connection A+ to F. It has a 409 concurrency guard, a 10 s watchdog and an SQM recommendation.

## v1.0.0 (2026-11-15)

- Multi-layer prober (gateway, WAN, DNS) with a scapy → icmp_dgram → ping → TCP fallback chain.
- Deterministic RCA engine with confidence scoring, chaos simulation, a SQLite incident log and an ISP CSV report.
- React + TypeScript + Tailwind NOC dashboard, Docker Compose setup and CI.
