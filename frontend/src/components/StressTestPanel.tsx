import { useState } from 'react';
import { Gauge, LoaderCircle, Play, ShieldAlert, X } from 'lucide-react';
import type { BufferbloatGrade, ChaosMode, StressTestStatus } from '../types/telemetry';
import { api } from '../lib/api';
import { cx, fmtMbps, fmtMs } from '../lib/format';

interface Props {
  status: StressTestStatus | null;
  chaosMode: ChaosMode;
  connected: boolean;
}

const GRADE_TONE: Record<BufferbloatGrade, string> = {
  'A+': 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10',
  A: 'text-emerald-300 border-emerald-500/50 bg-emerald-500/10',
  B: 'text-lime-300 border-lime-500/50 bg-lime-500/10',
  C: 'text-amber-300 border-amber-500/50 bg-amber-500/10',
  D: 'text-orange-300 border-orange-500/50 bg-orange-500/10',
  F: 'text-rose-300 border-rose-500/60 bg-rose-500/10',
  '?': 'text-slate-300 border-slate-600 bg-slate-800/50',
};

function Row({ label, idle, loaded }: { label: string; idle: string; loaded: string }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-baseline gap-3 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="num text-slate-300">{idle}</span>
      <span className="num text-slate-100">{loaded}</span>
    </div>
  );
}

export default function StressTestPanel({ status, chaosMode, connected }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);

  const running = status?.state === 'running';
  const chaosActive = chaosMode !== 'none';
  const disabled = !connected || running || chaosActive || starting;
  const result = status?.result ?? null;
  const showResult = !running && status && status.test_id !== dismissed && (result || status.error);

  const start = async () => {
    setError(null);
    setStarting(true);
    try {
      await api.startStressTest();
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^409 Conflict: /, '') : 'Request failed');
    } finally {
      setStarting(false);
    }
  };

  const title = chaosActive
    ? 'Disable the chaos simulation first'
    : running
      ? 'A test is running'
      : 'Saturate the link for 8 s and measure latency under load';

  return (
    <section className="panel flex flex-col p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="panel-title">
          <Gauge className="h-3.5 w-3.5 text-cyan-400" />
          Bufferbloat Test
        </h3>
        <span className="text-[11px] text-slate-500">loaded vs. unloaded latency</span>
      </div>

      <button
        type="button"
        onClick={() => void start()}
        disabled={disabled}
        title={title}
        className="flex items-center justify-center gap-2 rounded-lg border border-cyan-500/50 bg-cyan-500/15 px-3 py-2.5 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-500/25 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {running || starting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        {running ? 'Testing…' : 'Run Bufferbloat Test (8s)'}
      </button>
      {chaosActive && !running && (
        <p className="mt-2 text-[11px] text-slate-500">Available when the chaos simulation is set to Normal.</p>
      )}
      {error && <p className="mt-2 text-xs text-rose-400">{error}</p>}

      {running && status && (
        <div className="mt-3 space-y-2" aria-live="polite">
          <div className="h-2 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-fuchsia-500 transition-[width] duration-500 ease-linear"
              style={{ width: `${Math.round(status.progress * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-slate-400">
            <span className="num">
              ↓ {fmtMbps(status.live_download_mbps)} · ↑ {fmtMbps(status.live_upload_mbps)} Mb/s
            </span>
            <span className="num">{Math.max(0, status.duration_s * (1 - status.progress)).toFixed(1)}s left</span>
          </div>
        </div>
      )}

      {showResult && status && (
        <div className="relative mt-3 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
          <button
            type="button"
            onClick={() => setDismissed(status.test_id)}
            className="absolute right-2 top-2 text-slate-600 hover:text-slate-300"
            aria-label="Dismiss result"
          >
            <X className="h-3.5 w-3.5" />
          </button>
          {result ? (
            <>
              <div className="mb-3 flex items-center gap-3">
                <span className={cx('grid h-12 w-12 place-items-center rounded-lg border text-2xl font-bold', GRADE_TONE[result.grade])}>
                  {result.grade}
                </span>
                <div>
                  <p className="text-sm font-semibold text-slate-100">
                    {result.inflation_ms === null ? 'Probes lost under load' : `+${fmtMs(result.inflation_ms)} ms under load`}
                  </p>
                  <p className="num text-[11px] text-slate-500">
                    {result.inflation_multiplier ? `${result.inflation_multiplier.toFixed(1)}× idle latency · ` : ''}
                    {result.reference_hop === 'wan' ? 'Internet' : 'gateway'} RTT
                  </p>
                </div>
              </div>
              <div className="space-y-1">
                <div className="grid grid-cols-[1fr_auto_auto] gap-3 text-[10px] uppercase tracking-wider text-slate-600">
                  <span />
                  <span>Unloaded</span>
                  <span>Loaded</span>
                </div>
                <Row label="Gateway (median)" idle={`${fmtMs(result.unloaded_gateway_ms)} ms`} loaded={`${fmtMs(result.loaded_gateway_median_ms)} ms`} />
                <Row label="Internet (median)" idle={`${fmtMs(result.unloaded_wan_ms)} ms`} loaded={`${fmtMs(result.loaded_wan_median_ms)} ms`} />
                <Row label="Internet (P95)" idle="" loaded={`${fmtMs(result.loaded_wan_p95_ms)} ms`} />
                <Row label="Peak throughput" idle="" loaded={`↓${fmtMbps(result.peak_download_mbps)} ↑${fmtMbps(result.peak_upload_mbps)}`} />
              </div>
              {result.recommendation && (
                <p className="mt-3 flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/[0.07] p-2 text-[11px] leading-relaxed text-amber-100">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                  {result.recommendation}
                </p>
              )}
            </>
          ) : (
            <p className="pr-4 text-xs text-rose-300">
              Test {status.state}: {status.error}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
