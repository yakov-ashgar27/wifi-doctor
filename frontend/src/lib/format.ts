import type { HopStatus, RootCauseLayer } from '../types/telemetry';

export const fmtMs = (v: number | null | undefined, empty = '—'): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return empty;
  return v >= 100 ? `${v.toFixed(0)}` : v >= 10 ? v.toFixed(1) : v.toFixed(2);
};

export const fmtPct = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v.toFixed(v > 0 && v < 10 ? 1 : 0)}%`;

export const fmtMbps = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return '—';
  if (v < 0.01) return '0.00';
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
};

export const fmtDuration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

export const fmtClock = (epochMs: number): string =>
  new Date(epochMs).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

export const fmtDateTime = (iso: string): string =>
  new Date(iso).toLocaleString([], {
    hour12: false,
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

export const LAYER_LABEL: Record<RootCauseLayer, string> = {
  NONE: 'No fault',
  LOCAL_WIFI: 'Local Wi-Fi / LAN',
  ISP_WAN: 'ISP / WAN',
  DNS_SERVER: 'DNS Resolver',
  BUFFERBLOAT: 'Bufferbloat',
};

interface Tone {
  text: string;
  bg: string;
  border: string;
  dot: string;
  glow: string;
  line: string;
}

export const STATUS_TONE: Record<HopStatus, Tone> = {
  HEALTHY: {
    text: 'text-emerald-400',
    bg: 'bg-emerald-500/10',
    border: 'border-emerald-500/40',
    dot: 'bg-emerald-400',
    glow: 'shadow-[0_0_28px_-6px_rgba(16,185,129,0.55)]',
    line: 'text-emerald-500/70',
  },
  WARNING: {
    text: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/50',
    dot: 'bg-amber-400',
    glow: 'shadow-[0_0_30px_-4px_rgba(245,158,11,0.6)]',
    line: 'text-amber-500/80',
  },
  CRITICAL: {
    text: 'text-rose-400',
    bg: 'bg-rose-500/10',
    border: 'border-rose-500/60',
    dot: 'bg-rose-500',
    glow: 'shadow-[0_0_34px_-2px_rgba(244,63,94,0.7)]',
    line: 'text-rose-500/90',
  },
  UNKNOWN: {
    text: 'text-slate-400',
    bg: 'bg-slate-500/10',
    border: 'border-slate-600/60',
    dot: 'bg-slate-500',
    glow: '',
    line: 'text-slate-600',
  },
};

export const cx = (...parts: Array<string | false | null | undefined>): string => parts.filter(Boolean).join(' ');
