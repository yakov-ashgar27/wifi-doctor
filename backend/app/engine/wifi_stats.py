"""
Physical Wi-Fi layer telemetry: RSSI, band, channel, SSID and PHY link rates.

Every collector shells out to a platform tool and parses its text output with
pure functions (unit-tested against captured samples). Nothing here ever
raises: Ethernet hosts, containers without Wi-Fi hardware, missing tools,
localized output and permission errors all degrade to
``WifiInfo(is_wireless=False)`` or a partially filled record.

    Windows  netsh wlan show interfaces           (locale-tolerant parsing)
    Linux    iw dev <iface> link  ->  /proc/net/wireless
    macOS    airport -I  ->  system_profiler SPAirPortDataType

``collect_wifi_info`` is blocking; ``SystemMonitor`` runs it in a worker
thread every few seconds so the 1.5 s probe loop is never delayed.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

_AIRPORT = (
    "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
)


@dataclass
class WifiInfo:
    is_wireless: bool = False
    interface: str | None = None
    ssid: str | None = None
    rssi_dbm: int | None = None
    signal_pct: int | None = None
    rssi_estimated: bool = False          # True when dBm was derived from Signal %
    band: str | None = None               # "2.4 GHz" | "5 GHz" | "6 GHz"
    channel: int | None = None
    frequency_mhz: int | None = None
    radio_type: str | None = None         # e.g. 802.11ax
    rx_rate_mbps: float | None = None
    tx_rate_mbps: float | None = None
    source: str = "none"                  # which tool produced the data
    error: str | None = None
    simulated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def signal_quality(self) -> str | None:
        if self.rssi_dbm is None:
            return None
        if self.rssi_dbm >= -60:
            return "good"
        if self.rssi_dbm >= -72:
            return "fair"
        return "weak"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("extra", None)
        data["signal_quality"] = self.signal_quality
        return data


# ------------------------------------------------------------- band helpers
def band_from_frequency(mhz: float | None) -> str | None:
    if mhz is None:
        return None
    if 2400 <= mhz < 2500:
        return "2.4 GHz"
    if 4900 <= mhz < 5925:
        return "5 GHz"
    if 5925 <= mhz <= 7125:
        return "6 GHz"
    return None


def channel_from_frequency(mhz: float | None) -> int | None:
    if mhz is None:
        return None
    mhz = int(round(mhz))
    if mhz == 2484:
        return 14
    if 2412 <= mhz <= 2472:
        return (mhz - 2407) // 5
    if 5955 <= mhz <= 7115:
        return (mhz - 5950) // 5
    if 4910 <= mhz <= 5895:
        return (mhz - 5000) // 5
    return None


def band_from_channel(channel: int | None) -> str | None:
    """Best guess without a frequency (6 GHz channels overlap 5 GHz numbering)."""
    if channel is None:
        return None
    if 1 <= channel <= 14:
        return "2.4 GHz"
    if 32 <= channel <= 177:
        return "5 GHz"
    return None


def _normalize_band(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(2[.,]4|5|6)\s*G(?:Hz)?", text, re.I)
    if not m:
        return None
    return {"2.4": "2.4 GHz", "2,4": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}[m.group(1)]


def pct_to_dbm(pct: int) -> int:
    """Windows' documented mapping: 0 % = -100 dBm, 100 % = -50 dBm."""
    return int(round(pct / 2 - 100))


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    return float(m.group(0).replace(",", ".")) if m else None


# --------------------------------------------------------------- Windows
_KEY_VALUE = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")
_CHANNEL_KEYS = ("channel", "canal", "kanal", "canale", "kanaal", "ערוץ", "канал", "チャネル", "通道", "信道")
_STATE_DISCONNECTED = ("disconnected", "déconnecté", "getrennt", "desconectado", "disconnesso", "מנותק", "отключено")


def parse_netsh(text: str) -> WifiInfo:
    """Parse ``netsh wlan show interfaces``. Matches on value *shapes* where
    possible (``87%``, ``802.11ax``, ``5 GHz``) so localized Windows works."""
    if not text or not text.strip():
        return WifiInfo(source="netsh", error="empty output")
    lowered = text.lower()
    if "location" in lowered and "permission" in lowered:
        # Windows 11 24H2+: netsh WLAN data needs the Location privacy toggle.
        return WifiInfo(is_wireless=True, source="netsh", error="location-permission-required")

    # One interface per blank-line separated block (same in every locale).
    blocks: list[list[tuple[str, str]]] = [[]]
    for line in text.splitlines():
        if not line.strip():
            if blocks[-1]:
                blocks.append([])
            continue
        m = _KEY_VALUE.match(line)
        if m:
            blocks[-1].append((m.group(1).strip(), m.group(2).strip()))
    blocks = [b for b in blocks if b]

    best: WifiInfo | None = None
    for pairs in blocks:
        info = _parse_netsh_block(pairs)
        if info.ssid or info.signal_pct is not None:
            return info
        best = best or info
    return best or WifiInfo(source="netsh", error="no wireless interface")


def _parse_netsh_block(pairs: list[tuple[str, str]]) -> WifiInfo:
    info = WifiInfo(source="netsh")
    rates: list[float] = []
    disconnected = False
    for key, value in pairs:
        k = key.lower()
        if k in ("name", "nom", "nombre", "שם", "имя") and info.interface is None:
            info.interface = value or None
        elif key.upper() == "SSID":
            info.ssid = value or None
        elif re.fullmatch(r"\d{1,3}\s*%", value):
            info.signal_pct = int(value.rstrip("% ").strip())
        elif "rssi" in k:
            dbm = _num(value)
            info.rssi_dbm = int(dbm) if dbm is not None else None
        elif re.fullmatch(r"802\.11\s*\w+", value, re.I):
            info.radio_type = value.replace(" ", "")
        elif _normalize_band(value) and ("band" in k or "bande" in k or "banda" in k or "תדר" in k or re.fullmatch(r"(2[.,]4|5|6)\s*GHz", value, re.I)):
            info.band = _normalize_band(value)
        elif any(ck in k for ck in _CHANNEL_KEYS):
            ch = _num(value)
            info.channel = int(ch) if ch is not None else None
        elif "mbps" in k or "mbit" in k:
            rate = _num(value)
            if rate is not None:
                rates.append(rate)
        if any(word == value.lower() for word in _STATE_DISCONNECTED):
            disconnected = True

    # netsh prints Receive rate before Transmit rate in every locale.
    if rates:
        info.rx_rate_mbps = rates[0]
        info.tx_rate_mbps = rates[1] if len(rates) > 1 else None
    if info.rssi_dbm is None and info.signal_pct is not None:
        info.rssi_dbm = pct_to_dbm(info.signal_pct)
        info.rssi_estimated = True
    if info.band is None:
        info.band = band_from_channel(info.channel)
    info.is_wireless = bool(pairs) and not disconnected and (info.ssid is not None or info.signal_pct is not None)
    if disconnected:
        info.error = "disconnected"
    return info


# ----------------------------------------------------------------- Linux
def parse_iw_link(text: str, interface: str | None = None) -> WifiInfo:
    """Parse ``iw dev <iface> link``."""
    info = WifiInfo(interface=interface, source="iw")
    if not text or "not connected" in text.lower() or "connected to" not in text.lower():
        info.error = "not connected" if text else "empty output"
        return info
    for line in text.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("ssid:"):
            info.ssid = line.split(":", 1)[1].strip() or None
        elif low.startswith("freq:"):
            mhz = _num(line.split(":", 1)[1])
            info.frequency_mhz = int(mhz) if mhz else None
        elif low.startswith("signal:"):
            dbm = _num(line.split(":", 1)[1])
            info.rssi_dbm = int(dbm) if dbm is not None else None
        elif low.startswith("rx bitrate:"):
            info.rx_rate_mbps = _num(line.split(":", 1)[1])
            _set_radio_type(info, line)
        elif low.startswith("tx bitrate:"):
            info.tx_rate_mbps = _num(line.split(":", 1)[1])
            _set_radio_type(info, line)
    info.band = band_from_frequency(info.frequency_mhz)
    info.channel = channel_from_frequency(info.frequency_mhz)
    info.is_wireless = True
    return info


def _set_radio_type(info: WifiInfo, line: str) -> None:
    if info.radio_type:
        return
    for token, std in (("EHT", "802.11be"), ("HE-", "802.11ax"), ("VHT", "802.11ac"), ("MCS", "802.11n")):
        if token in line:
            info.radio_type = std
            return


def parse_proc_wireless(text: str, interface: str | None = None) -> WifiInfo:
    """Parse ``/proc/net/wireless`` (signal level only)."""
    info = WifiInfo(interface=interface, source="proc")
    for line in (text or "").splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if interface and name != interface:
            continue
        parts = rest.split()
        if len(parts) >= 3:
            level = _num(parts[2])
            if level is not None:
                level = int(level)
                # Old drivers report 0..255 unsigned
                info.rssi_dbm = level - 256 if level > 0 else level
            info.interface = name
            info.is_wireless = True
            return info
    info.error = "no wireless interface"
    return info


# ----------------------------------------------------------------- macOS
def parse_airport(text: str) -> WifiInfo:
    """Parse legacy ``airport -I`` output (removed in macOS 14.4+)."""
    info = WifiInfo(source="airport")
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            values[k.strip()] = v.strip()
    if not values or values.get("AirPort", "").lower() == "off" or "SSID" not in values:
        info.error = "not connected"
        return info
    info.ssid = values.get("SSID")
    rssi = _num(values.get("agrCtlRSSI"))
    info.rssi_dbm = int(rssi) if rssi is not None else None
    ch = _num((values.get("channel") or "").split(",")[0])
    info.channel = int(ch) if ch is not None else None
    info.band = band_from_channel(info.channel)
    info.tx_rate_mbps = _num(values.get("lastTxRate"))
    info.is_wireless = True
    return info


def parse_system_profiler(text: str) -> WifiInfo:
    """Parse ``system_profiler SPAirPortDataType`` "Current Network Information"."""
    info = WifiInfo(source="system_profiler")
    lines = (text or "").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if "Current Network Information" in l)
    except StopIteration:
        info.error = "not connected"
        return info
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        if stripped.endswith(":") and info.ssid is None:
            info.ssid = stripped[:-1]
        elif stripped.startswith("PHY Mode:"):
            info.radio_type = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Channel:"):
            val = stripped.split(":", 1)[1]
            ch = _num(val)
            info.channel = int(ch) if ch is not None else None
            info.band = _normalize_band(val) or band_from_channel(info.channel)
        elif stripped.startswith("Signal / Noise:"):
            dbm = _num(stripped.split(":", 1)[1])
            info.rssi_dbm = int(dbm) if dbm is not None else None
        elif stripped.startswith("Transmit Rate:"):
            info.tx_rate_mbps = _num(stripped.split(":", 1)[1])
    info.is_wireless = info.ssid is not None or info.rssi_dbm is not None
    return info


# ---------------------------------------------------------------- runner
def _run(cmd: list[str], timeout: float = 6.0) -> str:
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": timeout, "errors": "replace"}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, check=False, **kwargs)
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_wifi_info(interface: str | None = None) -> WifiInfo:
    """Blocking, never raises. Call from a worker thread."""
    try:
        if IS_WINDOWS:
            info = parse_netsh(_run(["netsh", "wlan", "show", "interfaces"]))
        elif IS_MAC:
            info = WifiInfo(source="none")
            if Path(_AIRPORT).exists():
                info = parse_airport(_run([_AIRPORT, "-I"]))
            if not info.is_wireless:
                info = parse_system_profiler(_run(["system_profiler", "SPAirPortDataType"], timeout=10))
        else:
            info = WifiInfo(source="none")
            if interface and shutil.which("iw"):
                info = parse_iw_link(_run(["iw", "dev", interface, "link"]), interface)
            if not info.is_wireless:
                try:
                    proc = Path("/proc/net/wireless").read_text()
                except OSError:
                    proc = ""
                fallback = parse_proc_wireless(proc, interface)
                if fallback.is_wireless:
                    info = fallback
        if info.interface is None:
            info.interface = interface
        return info
    except Exception as exc:  # pragma: no cover - belt and braces
        return WifiInfo(interface=interface, source="error", error=exc.__class__.__name__)
