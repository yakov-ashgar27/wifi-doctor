import { OctagonAlert, TriangleAlert, CircleCheckBig, FlaskConical, LoaderCircle, Wrench } from 'lucide-react';
import type { Diagnosis, TelemetryFrame } from '../types/telemetry';
import { LAYER_LABEL, cx } from '../lib/format';

interface Props {
  frame: TelemetryFrame | null;
}

const STYLE = {
  HEALTHY: {
    icon: CircleCheckBig,
    ring: 'border-emerald-500/30 from-emerald-500/[0.08]',
    accent: 'text-emerald-300',
    bar: 'bg-emerald-400',
    chip: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  },
  WARNING: {
    icon: TriangleAlert,
    ring: 'border-amber-500/40 from-amber-500/[0.10]',
    accent: 'text-amber-300',
    bar: 'bg-amber-400',
    chip: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
  },
  CRITICAL: {
    icon: OctagonAlert,
    ring: 'border-rose-500/50 from-rose-500/[0.12]',
    accent: 'text-rose-300',
    bar: 'bg-rose-500',
    chip: 'border-rose-500/50 bg-rose-500/10 text-rose-300',
  },
} as const;

const EVIDENCE_LABELS: Array<[keyof Diagnosis['evidence'] & string, string, string]> = [
  ['gateway_rtt_ms', 'Gateway', 'ms'],
  ['wan_rtt_ms', 'WAN', 'ms'],
  ['dns_ms', 'DNS', 'ms'],
  ['wan_loss_pct', 'WAN loss', '%'],
  ['utilization_pct', 'Link util', '%'],
];

export default function VerdictBanner({ frame }: Props) {
  if (!frame) {
    return (
      <section className="panel flex items-center gap-4 p-6">
        <LoaderCircle className="h-6 w-6 animate-spin text-cyan-400" />
        <div>
          <p className="text-lg font-semibold text-slate-200">Waiting for telemetry…</p>
          <p className="text-sm text-slate-500">Connecting to the WiFi-Doctor engine on port 8000.</p>
        </div>
      </section>
    );
  }

  const d = frame.diagnosis;
  const s = STYLE[d.status];
  const Icon = s.icon;
  const simulated = frame.chaos_mode !== 'none';

  return (
    <section
      className={cx(
        'relative overflow-hidden rounded-xl border bg-gradient-to-br to-slate-900/40 p-5 sm:p-6',
        s.ring,
      )}
      aria-live="polite"
    >
      {d.status !== 'HEALTHY' && (
        <div className={cx('absolute inset-y-0 left-0 w-1', s.bar)} aria-hidden />
      )}

      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <div className="flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cx('flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-bold tracking-wider', s.chip)}>
              <Icon className="h-3.5 w-3.5" />
              {d.status}
            </span>
            {d.root_cause_layer !== 'NONE' && (
              <span className="rounded-md border border-slate-700 bg-slate-800/70 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-slate-300">
                root cause · {LAYER_LABEL[d.root_cause_layer]}
              </span>
            )}
            {simulated && (
              <span className="flex items-center gap-1 rounded-md border border-fuchsia-500/40 bg-fuchsia-500/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-fuchsia-300">
                <FlaskConical className="h-3 w-3" /> simulation active
              </span>
            )}
          </div>

          <h2 className={cx('text-2xl font-semibold tracking-tight sm:text-3xl', d.status === 'HEALTHY' ? 'text-slate-100' : s.accent)}>
            {d.title}
          </h2>
          <p className="max-w-3xl text-sm leading-relaxed text-slate-300">{d.explanation}</p>

          <div className="flex flex-wrap gap-2 pt-1">
            {EVIDENCE_LABELS.map(([key, label, unit]) => {
              const v = d.evidence[key];
              if (v === null || v === undefined) return null;
              return (
                <span key={key} className="rounded border border-slate-800 bg-slate-950/50 px-2 py-1 text-[11px] text-slate-400">
                  {label} <span className="num text-slate-200">{typeof v === 'number' ? v.toFixed(v >= 100 ? 0 : 1) : v}{unit}</span>
                </span>
              );
            })}
          </div>
        </div>

        <div className="flex w-full flex-col gap-4 lg:w-[380px]">
          <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-4">
            <div className="flex items-baseline justify-between">
              <span className="text-xs uppercase tracking-[0.14em] text-slate-500">Confidence</span>
              <span className={cx('num text-3xl font-semibold', s.accent)}>{d.confidence_score}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
              <div className={cx('h-full rounded-full transition-all duration-700', s.bar)} style={{ width: `${d.confidence_score}%` }} />
            </div>
          </div>
          <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-cyan-300">
              <Wrench className="h-3.5 w-3.5" /> Recommended fix
            </p>
            <p className="text-sm leading-relaxed text-slate-200">{d.recommended_action}</p>
          </div>
        </div>
      </div>
    </section>
  );
}
