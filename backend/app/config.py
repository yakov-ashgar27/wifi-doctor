"""
Central configuration for WiFi-Doctor.

Every threshold used by the RCA engine lives here so the decision matrix can be
audited (and tuned) in one place. All values can be overridden with environment
variables of the same name, e.g. ``GATEWAY_WARN_MS=50``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str | None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # ------------------------------------------------------------------ app
    app_name: str = "WiFi-Doctor"
    version: str = "1.1.0"
    database_url: str = field(
        default_factory=lambda: _env_str(
            "DATABASE_URL", "sqlite+aiosqlite:///./data/wifi_doctor.db"
        )
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list(
            "CORS_ORIGINS",
            ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
        )
    )

    # ------------------------------------------------------------- probing
    probe_interval_s: float = field(default_factory=lambda: _env_float("PROBE_INTERVAL_S", 1.5))
    probe_timeout_s: float = field(default_factory=lambda: _env_float("PROBE_TIMEOUT_S", 1.0))
    wan_primary: str = field(default_factory=lambda: _env_str("WAN_PRIMARY", "1.1.1.1"))
    wan_backup: str = field(default_factory=lambda: _env_str("WAN_BACKUP", "8.8.8.8"))
    dns_domains: list[str] = field(
        default_factory=lambda: _env_list("DNS_DOMAINS", ["google.com", "cloudflare.com"])
    )
    # Optional manual overrides (useful inside Docker bridge networks)
    gateway_override: str | None = field(default_factory=lambda: _env_str("GATEWAY_IP", None))
    dns_override: str | None = field(default_factory=lambda: _env_str("DNS_SERVER", None))
    # Force a specific ICMP backend: scapy | icmp_dgram | subprocess | tcp
    force_probe_backend: str | None = field(
        default_factory=lambda: _env_str("PROBE_BACKEND", None)
    )
    network_redetect_s: float = 60.0

    # ------------------------------------------------------ rolling windows
    window_size: int = field(default_factory=lambda: _env_int("WINDOW_SIZE", 60))
    # Decisions use a shorter "recent" view so the verdict reacts in seconds,
    # while the UI still shows the full 60-sample (90 s) statistics.
    recent_rtt_samples: int = 8        # median of the last N successful RTTs
    recent_loss_samples: int = 20      # loss % over the last N probes (30 s)
    min_samples_for_verdict: int = 4
    # A loss trigger needs at least this many lost probes in the recent window:
    # one dropped ping out of 20 is 5 % and must not, by itself, raise an alarm.
    min_lost_probes: int = 2
    history_frames: int = 80           # frames replayed to newly connected clients

    # ------------------------------------------------------- RCA thresholds
    gateway_healthy_ms: float = field(default_factory=lambda: _env_float("GATEWAY_HEALTHY_MS", 15.0))
    gateway_warn_ms: float = field(default_factory=lambda: _env_float("GATEWAY_WARN_MS", 35.0))
    gateway_bloat_ms: float = field(default_factory=lambda: _env_float("GATEWAY_BLOAT_MS", 40.0))
    gateway_crit_ms: float = field(default_factory=lambda: _env_float("GATEWAY_CRIT_MS", 100.0))

    wan_bloat_ms: float = field(default_factory=lambda: _env_float("WAN_BLOAT_MS", 100.0))
    wan_warn_ms: float = field(default_factory=lambda: _env_float("WAN_WARN_MS", 120.0))
    wan_crit_ms: float = field(default_factory=lambda: _env_float("WAN_CRIT_MS", 300.0))

    dns_warn_ms: float = field(default_factory=lambda: _env_float("DNS_WARN_MS", 150.0))
    dns_crit_ms: float = field(default_factory=lambda: _env_float("DNS_CRIT_MS", 500.0))

    loss_warn_pct: float = field(default_factory=lambda: _env_float("LOSS_WARN_PCT", 3.0))
    loss_crit_pct: float = field(default_factory=lambda: _env_float("LOSS_CRIT_PCT", 10.0))

    local_bw_limit_mbps: float = field(default_factory=lambda: _env_float("LOCAL_BW_LIMIT_MBPS", 15.0))
    bloat_utilization_pct: float = field(default_factory=lambda: _env_float("BLOAT_UTILIZATION_PCT", 70.0))
    # Peak bandwidth never drops below this floor, so an idle machine does not
    # look "saturated" by a 50 kbps background blip.
    peak_floor_mbps: float = field(default_factory=lambda: _env_float("PEAK_FLOOR_MBPS", 10.0))
    peak_window_samples: int = 400     # ~10 minutes of history for observed peak

    # ------------------------------------------------------------ incidents
    incident_cooldown_s: float = field(default_factory=lambda: _env_float("INCIDENT_COOLDOWN_S", 30.0))
    incident_recovery_ticks: int = 3   # consecutive HEALTHY ticks to close an incident

    # ------------------------------------------- adaptive baselines (z-score)
    # Long rolling buffer of *good* RTT samples per hop (~10 min at 1.5 s).
    baseline_window_size: int = field(default_factory=lambda: _env_int("BASELINE_WINDOW_SIZE", 400))
    baseline_min_samples: int = field(default_factory=lambda: _env_int("BASELINE_MIN_SAMPLES", 60))
    zscore_threshold: float = field(default_factory=lambda: _env_float("ZSCORE_THRESHOLD", 3.5))
    # sigma = max(1.4826 * MAD, floor) so ultra-stable wired links do not explode the z-score
    baseline_sigma_floor_ms: float = field(default_factory=lambda: _env_float("BASELINE_SIGMA_FLOOR_MS", 1.0))
    # An anomaly also needs the RTT to clear a minimum noise floor per hop
    anomaly_floor_gateway_ms: float = field(default_factory=lambda: _env_float("ANOMALY_FLOOR_GATEWAY_MS", 15.0))
    anomaly_floor_wan_ms: float = field(default_factory=lambda: _env_float("ANOMALY_FLOOR_WAN_MS", 35.0))
    anomaly_floor_dns_ms: float = field(default_factory=lambda: _env_float("ANOMALY_FLOOR_DNS_MS", 45.0))
    # While a hop is anomalous only every Nth sample enters the baseline, so a
    # sustained fault cannot quickly redefine "normal" (but a permanent change
    # of network still converges eventually).
    baseline_anomaly_admit_every: int = 10

    # ------------------------------------------------------ system monitor
    top_process_refresh_s: float = 3.0

    # ------------------------------------------------ physical Wi-Fi layer
    wifi_refresh_s: float = field(default_factory=lambda: _env_float("WIFI_REFRESH_S", 4.0))
    wifi_weak_rssi_dbm: float = field(default_factory=lambda: _env_float("WIFI_WEAK_RSSI_DBM", -72.0))

    # ---------------------------------------------- bufferbloat stress test
    stress_duration_s: float = field(default_factory=lambda: _env_float("STRESS_DURATION_S", 8.0))
    stress_timeout_s: float = field(default_factory=lambda: _env_float("STRESS_TIMEOUT_S", 10.0))
    stress_download_workers: int = 3
    stress_upload_workers: int = 1
    stress_download_urls: list[str] = field(
        default_factory=lambda: _env_list(
            "STRESS_DOWNLOAD_URLS",
            [
                "https://speed.cloudflare.com/__down?bytes=25000000",
                "https://proof.ovh.net/files/10Mb.dat",
                "http://speedtest.tele2.net/10MB.zip",
            ],
        )
    )
    stress_upload_urls: list[str] = field(
        default_factory=lambda: _env_list("STRESS_UPLOAD_URLS", ["https://speed.cloudflare.com/__up"])
    )


settings = Settings()
