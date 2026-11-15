import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, FileWarning, FlaskConical, History, RefreshCw } from 'lucide-react';
import type { Incident, IncidentPage } from '../types/telemetry';
import { api } from '../lib/api';
import { LAYER_LABEL, cx, fmtDateTime, fmtDuration, fmtMbps, fmtMs, fmtPct } from '../lib/format';

interface Props {
  refreshKey: number;
}

const PAGE_SIZE = 8;

function SeverityBadge({ severity }: { severity: Incident['severity'] }) {
  return (
    <span
      className={cx(
        'rounded border px-1.5 py-0.5 text-[10px] font-bold tracking-wider',
        severity === 'CRITICAL'
          ? 'border-rose-500/50 bg-rose-500/10 text-rose-300'
          : 'border-amber-500/50 bg-amber-500/10 text-amber-300',
      )}
    >
      {severity}
    </span>
  );
}

function liveDuration(inc: Incident): number {
  if (inc.duration_s !== null) return inc.duration_s;
  return (Date.now() - new Date(inc.started_at).getTime()) / 1000;
}

export default function IncidentTable({ refreshKey }: Props) {
  const [page, setPage] = useState(1);
  const [realOnly, setRealOnly] = useState(false);
  const [data, setData] = useState<IncidentPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.incidents(page, PAGE_SIZE, realOnly);
      setData(res);
      setError(null);
      if (page > res.pages) setPage(res.pages);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load incidents');
    } finally {
      setLoading(false);
    }
  }, [page, realOnly]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // Tick open incidents' durations without refetching.
  const [, force] = useState(0);
  useEffect(() => {
    if (!data?.items.some((i) => i.resolved_at === null)) return;
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [data]);

  return (
    <section className="panel p-4">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h3 className="panel-title">
          <History className="h-3.5 w-3.5" /> Incident History
          {data && <span className="num ml-1 rounded bg-slate-800 px-1.5 text-[10px] text-slate-300">{data.total}</span>}
        </h3>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={realOnly}
              onChange={(e) => {
                setRealOnly(e.target.checked);
                setPage(1);
              }}
              className="h-3.5 w-3.5 accent-cyan-500"
            />
            Real incidents only
          </label>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-md border border-slate-700 p-1.5 text-slate-400 hover:border-slate-500 hover:text-slate-200"
            aria-label="Refresh incidents"
          >
            <RefreshCw className={cx('h-3.5 w-3.5', loading && 'animate-spin')} />
          </button>
          <a
            href={api.exportUrl(realOnly)}
            className="flex items-center gap-2 rounded-md border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-semibold text-cyan-200 transition hover:bg-cyan-500/20"
          >
            <Download className="h-3.5 w-3.5" />
            Download ISP Evidence Report (CSV)
          </a>
        </div>
      </div>

      {error && <p className="mb-3 text-xs text-rose-400">{error}</p>}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
              <th className="px-2 py-2 font-medium">Started</th>
              <th className="px-2 py-2 font-medium">Duration</th>
              <th className="px-2 py-2 font-medium">Severity</th>
              <th className="px-2 py-2 font-medium">Layer</th>
              <th className="px-2 py-2 font-medium">Diagnosis</th>
              <th className="px-2 py-2 text-right font-medium">GW ms</th>
              <th className="px-2 py-2 text-right font-medium">WAN ms</th>
              <th className="px-2 py-2 text-right font-medium">WAN loss</th>
              <th className="px-2 py-2 text-right font-medium">DNS ms</th>
              <th className="px-2 py-2 text-right font-medium">Conf.</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((inc) => (
              <IncidentRow
                key={inc.id}
                inc={inc}
                expanded={expanded === inc.id}
                onToggle={() => setExpanded((cur) => (cur === inc.id ? null : inc.id))}
              />
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-slate-500">
            <FileWarning className="h-6 w-6 text-slate-600" />
            No incidents recorded yet. Trigger a chaos simulation to generate one.
          </div>
        )}
      </div>

      {data && data.pages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-slate-400">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded border border-slate-700 p-1 disabled:opacity-30"
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="num">
            {page} / {data.pages}
          </span>
          <button
            type="button"
            disabled={page >= data.pages}
            onClick={() => setPage((p) => p + 1)}
            className="rounded border border-slate-700 p-1 disabled:opacity-30"
            aria-label="Next page"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </section>
  );
}

function IncidentRow({ inc, expanded, onToggle }: { inc: Incident; expanded: boolean; onToggle: () => void }) {
  const open = inc.resolved_at === null;
  return (
    <>
      <tr onClick={onToggle} className="cursor-pointer border-b border-slate-800/60 transition hover:bg-slate-800/30">
        <td className="num whitespace-nowrap px-2 py-2 text-xs text-slate-300">{fmtDateTime(inc.started_at)}</td>
        <td className="num whitespace-nowrap px-2 py-2 text-xs">
          {open ? (
            <span className="flex items-center gap-1.5 text-rose-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-rose-400" />
              {fmtDuration(liveDuration(inc))}
            </span>
          ) : (
            <span className="text-slate-300">{fmtDuration(inc.duration_s)}</span>
          )}
        </td>
        <td className="px-2 py-2">
          <SeverityBadge severity={inc.severity} />
        </td>
        <td className="whitespace-nowrap px-2 py-2 font-mono text-[11px] text-slate-300">{LAYER_LABEL[inc.root_cause_layer]}</td>
        <td className="px-2 py-2 text-xs text-slate-200">
          <span className="flex items-center gap-1.5">
            {inc.simulated && (
              <span title="Generated by a chaos simulation" className="text-fuchsia-400">
                <FlaskConical className="h-3.5 w-3.5" />
              </span>
            )}
            {inc.title}
          </span>
        </td>
        <td className="num px-2 py-2 text-right text-xs text-slate-300">{fmtMs(inc.gateway_rtt_ms)}</td>
        <td className="num px-2 py-2 text-right text-xs text-slate-300">{fmtMs(inc.wan_rtt_ms)}</td>
        <td className="num px-2 py-2 text-right text-xs text-slate-300">{fmtPct(inc.wan_loss_pct)}</td>
        <td className="num px-2 py-2 text-right text-xs text-slate-300">{fmtMs(inc.dns_ms)}</td>
        <td className="num px-2 py-2 text-right text-xs text-cyan-300">{inc.confidence_score}%</td>
      </tr>
      {expanded && (
        <tr className="border-b border-slate-800/60 bg-slate-950/40">
          <td colSpan={10} className="px-3 py-3">
            <div className="grid gap-3 text-xs md:grid-cols-[2fr_1fr]">
              <div className="space-y-2">
                <p className="leading-relaxed text-slate-300">{inc.explanation}</p>
                <p className="leading-relaxed text-cyan-200/90">
                  <span className="font-semibold text-cyan-300">Fix: </span>
                  {inc.recommended_action}
                </p>
              </div>
              <dl className="num grid grid-cols-2 gap-x-3 gap-y-1 text-slate-400">
                <dt>Gateway</dt>
                <dd className="text-slate-200">{inc.gateway_ip ?? '—'} · {fmtPct(inc.gateway_loss_pct)} loss</dd>
                <dt>WAN</dt>
                <dd className="text-slate-200">{inc.wan_target ?? '—'} · {fmtMs(inc.wan_jitter_ms)} ms jitter</dd>
                <dt>DNS</dt>
                <dd className="text-slate-200">{inc.dns_server ?? '—'} · {fmtPct(inc.dns_loss_pct)} fail</dd>
                <dt>Traffic</dt>
                <dd className="text-slate-200">↓{fmtMbps(inc.download_mbps)} ↑{fmtMbps(inc.upload_mbps)} Mb/s</dd>
                <dt>Top process</dt>
                <dd className="text-slate-200">{inc.top_process ?? '—'}</dd>
                <dt>Probe</dt>
                <dd className="text-slate-200">{inc.probe_backend ?? '—'}</dd>
              </dl>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
