"""
Active multi-layer network prober.

Hop 1  Gateway   ICMP echo to the default gateway (local Wi-Fi / LAN segment)
Hop 2  WAN       ICMP echo to 1.1.1.1, with 8.8.8.8 raced as backup
Hop 3  DNS       Real UDP/53 A-record query against the system resolver

ICMP backends are tried in order of fidelity and the prober degrades
gracefully instead of crashing:

    scapy        raw L3 sockets (root / Administrator + Npcap / CAP_NET_RAW)
    icmp_dgram   unprivileged ICMP datagram sockets (Linux ping_group_range, macOS)
    subprocess   the OS ``ping`` binary, RTT parsed via regex (works on stock Windows)
    tcp          TCP handshake RTT (SYN -> SYN/ACK or RST) - always available

DNS never needs privileges: it is a hand-built DNS packet sent over a normal
UDP socket, so the measured time is true resolver latency.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import logging
import os
import platform
import random
import re
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from ..config import Settings

log = logging.getLogger("wifi_doctor.prober")

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

HOP_GATEWAY = "gateway"
HOP_WAN = "wan"
HOP_DNS = "dns"


# ------------------------------------------------------------------ models
@dataclass
class ProbeSample:
    hop: str
    target: str | None
    rtt_ms: float | None          # None => timeout / failure (counts as loss)
    method: str
    error: str | None = None
    simulated: bool = False

    @property
    def ok(self) -> bool:
        return self.rtt_ms is not None


@dataclass
class NetworkInfo:
    hostname: str = field(default_factory=socket.gethostname)
    platform: str = field(default_factory=lambda: f"{platform.system()} {platform.release()}")
    interface: str | None = None
    local_ip: str | None = None
    gateway_ip: str | None = None
    dns_server: str | None = None
    probe_backend: str = "detecting"

    def to_dict(self) -> dict:
        return {
            "hostname": self.hostname,
            "platform": self.platform,
            "interface": self.interface,
            "local_ip": self.local_ip,
            "gateway_ip": self.gateway_ip,
            "dns_server": self.dns_server,
            "probe_backend": self.probe_backend,
        }


class BackendUnavailable(RuntimeError):
    """The ICMP backend cannot run on this host (permissions, missing driver...)."""


# --------------------------------------------------------- shell helpers
def _run(cmd: list[str], timeout: float = 4.0) -> str:
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    try:
        return subprocess.run(cmd, check=False, **kwargs).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_ipv4(value: str | None) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value or ""), ipaddress.IPv4Address)
    except ValueError:
        return False


# ------------------------------------------------------ network discovery
def detect_local_ip() -> str | None:
    """The source IP the OS would use for Internet traffic (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            return s.getsockname()[0]
    except OSError:
        return None


def interface_for_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if any(a.family == socket.AF_INET and a.address == ip for a in addrs):
                return name
    except Exception:  # pragma: no cover - psutil edge cases
        pass
    return None


def _gateway_linux() -> tuple[str | None, str | None]:
    try:
        lines = Path("/proc/net/route").read_text().splitlines()[1:]
    except OSError:
        lines = []
    best: tuple[int, str, str] | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 8 or parts[1] != "00000000":
            continue
        flags = int(parts[3], 16)
        if not flags & 0x2:  # RTF_GATEWAY
            continue
        gw = socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
        metric = int(parts[6])
        if best is None or metric < best[0]:
            best = (metric, gw, parts[0])
    if best:
        return best[1], best[2]
    out = _run(["ip", "-4", "route", "show", "default"])
    m = re.search(r"default via (\S+)(?: dev (\S+))?", out)
    return (m.group(1), m.group(2)) if m else (None, None)


def _gateway_windows() -> tuple[str | None, str | None]:
    out = _run(["route", "print", "-4", "0.0.0.0"])
    best: tuple[int, str, str] | None = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0" and _is_ipv4(parts[2]):
            try:
                metric = int(parts[4])
            except ValueError:
                continue
            if best is None or metric < best[0]:
                best = (metric, parts[2], parts[3])
    if best:
        return best[1], interface_for_ip(best[2])
    return None, None


def _gateway_mac() -> tuple[str | None, str | None]:
    out = _run(["route", "-n", "get", "default"])
    gw = re.search(r"gateway:\s*(\S+)", out)
    iface = re.search(r"interface:\s*(\S+)", out)
    return (gw.group(1) if gw else None, iface.group(1) if iface else None)


def _gateway_scapy() -> str | None:
    try:
        from scapy.config import conf  # type: ignore

        gw = conf.route.route("0.0.0.0")[2]
        return gw if _is_ipv4(gw) and gw != "0.0.0.0" else None
    except Exception:
        return None


def detect_gateway() -> tuple[str | None, str | None]:
    if IS_WINDOWS:
        gw, iface = _gateway_windows()
    elif IS_MAC:
        gw, iface = _gateway_mac()
    else:
        gw, iface = _gateway_linux()
    if not _is_ipv4(gw):
        gw = _gateway_scapy()
    return gw, iface


def detect_dns_server(gateway: str | None) -> str | None:
    candidates: list[str] = []
    if IS_WINDOWS:
        out = _run(["ipconfig", "/all"], timeout=6)
        capture = False
        for line in out.splitlines():
            if "DNS" in line and ":" in line:
                capture = True
                candidates += re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line.split(":", 1)[1])
            elif capture and line.startswith(" " * 20):
                candidates += re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
            else:
                capture = False
    else:
        # Prefer the real upstream behind systemd-resolved's 127.0.0.53 stub.
        for path in ("/run/systemd/resolve/resolv.conf", "/etc/resolv.conf"):
            try:
                text = Path(path).read_text()
            except OSError:
                continue
            candidates += re.findall(r"^\s*nameserver\s+(\S+)", text, re.MULTILINE)
            if candidates:
                break
    for c in candidates:
        if _is_ipv4(c) and not c.startswith("0."):
            return c
    return gateway or "1.1.1.1"


# ------------------------------------------------------------ ICMP helpers
def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


_seq = itertools.count(random.randint(0, 30000))
_ICMP_ID = os.getpid() & 0xFFFF


class Pinger:
    name = "base"

    async def ping(self, host: str, timeout: float) -> float | None:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def available(cls) -> bool:  # pragma: no cover
        return True


class ScapyPinger(Pinger):
    """Raw-socket ICMP via scapy (most accurate kernel timestamps)."""

    name = "scapy"

    @classmethod
    def available(cls) -> bool:
        try:
            import logging as _logging

            _logging.getLogger("scapy.runtime").setLevel(_logging.ERROR)
            from scapy.config import conf  # type: ignore

            conf.verb = 0
            sock = conf.L3socket()  # raises without CAP_NET_RAW / admin / Npcap
            sock.close()
            return True
        except Exception as exc:  # ImportError, PermissionError, Scapy_Exception, OSError...
            log.info("scapy backend unavailable: %s", exc.__class__.__name__)
            return False

    @staticmethod
    def _ping_sync(host: str, timeout: float) -> float | None:
        from scapy.layers.inet import ICMP, IP  # type: ignore
        from scapy.sendrecv import sr  # type: ignore

        seq = next(_seq) & 0xFFFF
        pkt = IP(dst=host) / ICMP(id=_ICMP_ID, seq=seq)
        t0 = time.perf_counter()
        try:
            answered, _ = sr(pkt, timeout=timeout, verbose=0, retry=0)
        except PermissionError as exc:
            raise BackendUnavailable(str(exc)) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) in (1, 13):  # EPERM / EACCES
                raise BackendUnavailable(str(exc)) from exc
            return None
        if not answered:
            return None
        sent, reply = answered[0]
        if reply.haslayer(ICMP) and reply[ICMP].type != 0:
            return None  # unreachable / TTL exceeded
        sent_time = getattr(sent, "sent_time", None)
        if sent_time and getattr(reply, "time", None):
            rtt = (float(reply.time) - float(sent_time)) * 1000.0
            if 0 <= rtt < timeout * 1000 * 2:
                return rtt
        return (time.perf_counter() - t0) * 1000.0

    async def ping(self, host: str, timeout: float) -> float | None:
        return await asyncio.to_thread(self._ping_sync, host, timeout)


class IcmpDatagramPinger(Pinger):
    """Unprivileged ICMP (SOCK_DGRAM + IPPROTO_ICMP) - Linux & macOS."""

    name = "icmp_dgram"

    @classmethod
    def available(cls) -> bool:
        if IS_WINDOWS:
            return False
        try:
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
            return True
        except OSError:
            return False

    async def ping(self, host: str, timeout: float) -> float | None:
        loop = asyncio.get_running_loop()
        seq = next(_seq) & 0xFFFF
        payload = b"wifi-doctor-probe".ljust(32, b".")
        header = struct.pack("!BBHHH", 8, 0, 0, _ICMP_ID, seq)
        packet = struct.pack("!BBHHH", 8, 0, _checksum(header + payload), _ICMP_ID, seq) + payload
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        except PermissionError as exc:
            raise BackendUnavailable(str(exc)) from exc
        sock.setblocking(False)
        try:
            sock.connect((host, 0))
            t0 = time.perf_counter()
            await loop.sock_sendall(sock, packet)
            deadline = t0 + timeout
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return None
                data = await asyncio.wait_for(loop.sock_recv(sock, 2048), remaining)
                rtt = (time.perf_counter() - t0) * 1000.0
                if data and data[0] >> 4 == 4:  # macOS includes the IP header
                    data = data[(data[0] & 0x0F) * 4 :]
                if len(data) >= 8:
                    icmp_type, _, _, _, r_seq = struct.unpack("!BBHHH", data[:8])
                    if icmp_type == 0 and r_seq == seq:
                        return rtt
        except (asyncio.TimeoutError, TimeoutError):
            return None
        except OSError:
            return None  # host unreachable etc. -> counts as loss
        finally:
            sock.close()


class SubprocessPinger(Pinger):
    """Shells out to the OS ``ping`` binary and parses RTT with a regex."""

    name = "subprocess"
    _rtt_re = re.compile(
        r"(?:time|zeit|temps|tiempo|tempo|tijd|czas|время|זמן)\s*[=<]\s*([\d.,]+)\s*ms", re.I
    )
    _rtt_fallback_re = re.compile(r"[=<]\s*([\d.,]+)\s*ms", re.I)

    @classmethod
    def available(cls) -> bool:
        return shutil.which("ping") is not None

    def _cmd(self, host: str, timeout: float) -> list[str]:
        if IS_WINDOWS:
            return ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        if IS_MAC:
            return ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
        return ["ping", "-c", "1", "-W", str(max(1, round(timeout))), "-n", host]

    def _ping_sync(self, host: str, timeout: float) -> float | None:
        kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout + 1.5}
        if IS_WINDOWS:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            kwargs["errors"] = "replace"
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(self._cmd(host, timeout), check=False, **kwargs)
        except FileNotFoundError as exc:
            raise BackendUnavailable("ping binary not found") from exc
        except subprocess.TimeoutExpired:
            return None
        wall = (time.perf_counter() - t0) * 1000.0
        out = proc.stdout or ""
        if proc.returncode != 0:
            if "operation not permitted" in (out + (proc.stderr or "")).lower():
                raise BackendUnavailable("ping not permitted")
            return None
        if IS_WINDOWS and "TTL=" not in out.upper():
            return None  # "Destination host unreachable" still returns 0 on Windows
        m = self._rtt_re.search(out) or self._rtt_fallback_re.search(out)
        if m:
            try:
                return max(float(m.group(1).replace(",", ".")), 0.1)
            except ValueError:
                pass
        return wall  # parsing failed but ping succeeded: wall clock is an upper bound

    async def ping(self, host: str, timeout: float) -> float | None:
        # A thread (not asyncio subprocess) works with every event loop policy,
        # including the SelectorEventLoop uvicorn may install on Windows.
        return await asyncio.to_thread(self._ping_sync, host, timeout)


class TcpPinger(Pinger):
    """TCP handshake latency. A RST (connection refused) is still a valid RTT."""

    name = "tcp"
    ports = (53, 443, 80)

    async def _connect(self, host: str, port: int, timeout: float) -> float | None:
        t0 = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            rtt = (time.perf_counter() - t0) * 1000.0
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.2)
            except Exception:
                pass
            return rtt
        except ConnectionRefusedError:
            return (time.perf_counter() - t0) * 1000.0
        except (asyncio.TimeoutError, TimeoutError, OSError):
            return None

    async def ping(self, host: str, timeout: float) -> float | None:
        tasks = [asyncio.create_task(self._connect(host, p, timeout)) for p in self.ports]
        try:
            for fut in asyncio.as_completed(tasks):
                result = await fut
                if result is not None:
                    return result
            return None
        finally:
            for t in tasks:
                t.cancel()


BACKENDS: dict[str, type[Pinger]] = {
    "scapy": ScapyPinger,
    "icmp_dgram": IcmpDatagramPinger,
    "subprocess": SubprocessPinger,
    "tcp": TcpPinger,
}
BACKEND_ORDER = ["scapy", "icmp_dgram", "subprocess", "tcp"]


# ---------------------------------------------------------------- DNS probe
def build_dns_query(domain: str, txid: int) -> bytes:
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)  # RD=1, 1 question
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in domain.strip(".").split(".")) + b"\x00"
    return header + qname + struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN


class _DnsProtocol(asyncio.DatagramProtocol):
    def __init__(self, txid: int, future: asyncio.Future):
        self.txid = txid
        self.future = future

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) >= 12 and struct.unpack("!H", data[:2])[0] == self.txid and not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


async def dns_query_time(server: str, domain: str, timeout: float) -> tuple[float | None, str | None]:
    loop = asyncio.get_running_loop()
    txid = random.randint(0, 0xFFFF)
    future: asyncio.Future = loop.create_future()
    transport = None
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DnsProtocol(txid, future), remote_addr=(server, 53)
        )
        t0 = time.perf_counter()
        transport.sendto(build_dns_query(domain, txid))
        data = await asyncio.wait_for(future, timeout)
        rtt = (time.perf_counter() - t0) * 1000.0
        rcode = data[3] & 0x0F
        if rcode in (0, 3):  # NOERROR / NXDOMAIN: resolver answered
            return rtt, None
        return None, f"rcode={rcode}"
    except (asyncio.TimeoutError, TimeoutError):
        return None, "timeout"
    except OSError as exc:
        return None, exc.__class__.__name__
    finally:
        if transport is not None:
            transport.close()


# ------------------------------------------------------------------- prober
class Prober:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.network = NetworkInfo()
        self._chain: list[str] = []
        self._pinger: Pinger = TcpPinger()
        self._last_detect = 0.0
        self._dns_cycle = itertools.cycle(settings.dns_domains or ["google.com"])
        self._backend_errors = 0
        self._all_icmp_failed_ticks = 0

    # ---------------------------------------------------------------- setup
    async def initialize(self) -> None:
        await self.refresh_network(force=True)
        forced = self.settings.force_probe_backend
        order = [forced] + [b for b in BACKEND_ORDER if b != forced] if forced in BACKENDS else BACKEND_ORDER
        chain = []
        for name in order:
            ok = await asyncio.to_thread(BACKENDS[name].available)
            if ok:
                chain.append(name)
        self._chain = chain or ["tcp"]
        self._select(self._chain[0])
        log.info("probe backend chain: %s", " -> ".join(self._chain))

    def _select(self, name: str) -> None:
        self._pinger = BACKENDS[name]()
        self.network.probe_backend = name
        self._backend_errors = 0
        self._all_icmp_failed_ticks = 0

    def _demote(self, reason: str) -> None:
        current = self._pinger.name
        idx = self._chain.index(current) if current in self._chain else -1
        if idx + 1 < len(self._chain):
            nxt = self._chain[idx + 1]
        elif current != "tcp":
            nxt = "tcp"
        else:
            return
        log.warning("probe backend %s demoted (%s) -> %s", current, reason, nxt)
        self._select(nxt)

    async def refresh_network(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_detect < self.settings.network_redetect_s:
            return
        self._last_detect = now

        def _detect() -> tuple[str | None, str | None, str | None, str | None]:
            local_ip = detect_local_ip()
            gw, iface = detect_gateway()
            gw = self.settings.gateway_override or gw
            dns = self.settings.dns_override or detect_dns_server(gw)
            return local_ip, gw, iface or interface_for_ip(local_ip), dns

        local_ip, gw, iface, dns = await asyncio.to_thread(_detect)
        if (gw, dns) != (self.network.gateway_ip, self.network.dns_server):
            log.info("network: iface=%s ip=%s gateway=%s dns=%s", iface, local_ip, gw, dns)
        self.network.local_ip = local_ip
        self.network.gateway_ip = gw
        self.network.interface = iface
        self.network.dns_server = dns

    # ---------------------------------------------------------------- probes
    async def _ping(self, host: str) -> tuple[float | None, str | None]:
        try:
            return await self._pinger.ping(host, self.settings.probe_timeout_s), None
        except BackendUnavailable as exc:
            self._demote(str(exc))
            return None, "backend-unavailable"
        except Exception as exc:  # never let a probe crash the loop
            self._backend_errors += 1
            if self._backend_errors >= 3:
                self._demote(repr(exc))
            return None, exc.__class__.__name__

    async def probe_gateway(self) -> ProbeSample:
        gw = self.network.gateway_ip
        method = self._pinger.name
        if not gw:
            return ProbeSample(HOP_GATEWAY, None, None, method, "no-gateway")
        rtt, err = await self._ping(gw)
        return ProbeSample(HOP_GATEWAY, gw, rtt, method, err if rtt is None else None)

    async def probe_wan(self) -> ProbeSample:
        method = self._pinger.name
        primary = asyncio.create_task(self._ping(self.settings.wan_primary))
        backup = asyncio.create_task(self._ping(self.settings.wan_backup))
        p_rtt, p_err = await primary
        if p_rtt is not None:
            backup.cancel()
            return ProbeSample(HOP_WAN, self.settings.wan_primary, p_rtt, method)
        b_rtt, b_err = await backup
        if b_rtt is not None:
            return ProbeSample(HOP_WAN, self.settings.wan_backup, b_rtt, method)
        return ProbeSample(HOP_WAN, self.settings.wan_primary, None, method, p_err or b_err or "timeout")

    async def probe_dns(self) -> ProbeSample:
        server = self.network.dns_server or "1.1.1.1"
        domain = next(self._dns_cycle)
        rtt, err = await dns_query_time(server, domain, self.settings.probe_timeout_s)
        return ProbeSample(HOP_DNS, server, rtt, "udp53", err)

    async def probe_all(self) -> dict[str, ProbeSample]:
        await self.refresh_network()
        gw, wan, dns = await asyncio.gather(self.probe_gateway(), self.probe_wan(), self.probe_dns())
        await self._check_icmp_blackhole(gw, wan, dns)
        return {HOP_GATEWAY: gw, HOP_WAN: wan, HOP_DNS: dns}

    async def _check_icmp_blackhole(self, gw: ProbeSample, wan: ProbeSample, dns: ProbeSample) -> None:
        """If ICMP silently fails while DNS (UDP) works, ICMP is filtered: fall back to TCP."""
        if self._pinger.name == "tcp":
            return
        if not gw.ok and not wan.ok and dns.ok:
            self._all_icmp_failed_ticks += 1
        else:
            self._all_icmp_failed_ticks = 0
        if self._all_icmp_failed_ticks >= 8:
            self._all_icmp_failed_ticks = 0
            if await TcpPinger().ping(self.settings.wan_primary, self.settings.probe_timeout_s) is not None:
                self._demote("ICMP appears filtered on this network")
