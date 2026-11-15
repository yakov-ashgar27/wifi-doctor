import { useEffect, useState } from 'react';
import { Activity, Cpu, RefreshCw, Stethoscope, Wifi, WifiOff } from 'lucide-react';
import type { ConnectionState, TelemetryFrame } from '../types/telemetry';
import { cx } from '../lib/format';

interface Props {
  connection: ConnectionState;
  reconnectAttempt: number;
  latest: TelemetryFrame | null;
  onReconnect: () => void;
}

const CONNECTION_META: Record<ConnectionState, { label: string; tone: string; dot: string }> = {
  open: { label: 'Live', tone: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300', dot: 'bg-emerald-400' },
  connecting: { label: 'Connecting', tone: 'border-sky-500/40 bg-sky-500/10 text-sky-300', dot: 'bg-sky-400' },
  reconnecting: { label: 'Reconnecting', tone: 'border-amber-500/40 bg-amber-500/10 text-amber-300', dot: 'bg-amber-400' },
  offline: { label: 'Offline', tone: 'border-rose-500/40 bg-rose-500/10 text-rose-300', dot: 'bg-rose-500' },
};

const BACKEND_LABEL: Record<string, string> = {
  scapy: 'ICMP · scapy raw',
  icmp_dgram: 'ICMP · unprivileged',
  subprocess: 'ICMP · system ping',
  tcp: 'TCP handshake',
  detecting: 'detecting…',
};

export default function Header({ connection, reconnectAttempt, latest, onReconnect }: Props) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const meta = CONNECTION_META[connection];
  const net = latest?.network;

  return (
    <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
            <Stethoscope className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-base font-semibold tracking-tight text-slate-100">
              WiFi<span className="text-cyan-400">-</span>Doctor
            </h1>
            <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Network RCA Console</p>
          </div>
        </div>

        <div className="hidden items-center gap-4 text-xs text-slate-400 md:flex">
          {net && (
            <>
              <span className="flex items-center gap-1.5">
                <Cpu className="h-3.5 w-3.5 text-slate-500" />
                <span className="num text-slate-300">{net.hostname}</span>
              </span>
              <span className="flex items-center gap-1.5">
                <Wifi className="h-3.5 w-3.5 text-slate-500" />
                <span className="num text-slate-300">{net.interface ?? 'n/a'}</span>
              </span>
              <span className="flex items-center gap-1.5" title="Active probe method (auto-selected with graceful fallback)">
                <Activity className="h-3.5 w-3.5 text-slate-500" />
                <span className="num text-slate-300">{BACKEND_LABEL[net.probe_backend] ?? net.probe_backend}</span>
              </span>
            </>
          )}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <span className="num hidden text-sm text-slate-300 sm:inline">
            {now.toLocaleTimeString([], { hour12: false })}
          </span>
          <button
            type="button"
            onClick={onReconnect}
            disabled={connection === 'open'}
            title={connection === 'open' ? 'Streaming telemetry' : 'Reconnect now'}
            className={cx(
              'flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition',
              meta.tone,
              connection !== 'open' && 'hover:brightness-125',
            )}
          >
            <span className="relative flex h-2 w-2">
              {connection === 'open' && (
                <span className={cx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', meta.dot)} />
              )}
              <span className={cx('relative inline-flex h-2 w-2 rounded-full', meta.dot)} />
            </span>
            {meta.label}
            {connection === 'reconnecting' && reconnectAttempt > 0 && (
              <span className="num opacity-70">#{reconnectAttempt}</span>
            )}
            {connection === 'offline' && <WifiOff className="h-3.5 w-3.5" />}
            {connection === 'reconnecting' && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
          </button>
        </div>
      </div>
    </header>
  );
}
