import { useState } from 'react';
import { CircleCheck, FlaskConical, Gauge, Globe, LoaderCircle, type LucideIcon, Server, WifiOff } from 'lucide-react';
import type { ChaosMode } from '../types/telemetry';
import { api } from '../lib/api';
import { cx } from '../lib/format';

interface Props {
  mode: ChaosMode;
  disabled?: boolean;
}

const OPTIONS: Array<{ mode: ChaosMode; label: string; hint: string; icon: LucideIcon; active: string }> = [
  { mode: 'none', label: 'Normal', hint: 'Real telemetry only', icon: CircleCheck, active: 'border-emerald-500/60 bg-emerald-500/15 text-emerald-200' },
  { mode: 'wifi_lag', label: 'Simulate Wi-Fi Lag', hint: '+40–160 ms & 8% loss on hop 1', icon: WifiOff, active: 'border-amber-500/60 bg-amber-500/15 text-amber-200' },
  { mode: 'isp_loss', label: 'Simulate ISP Loss', hint: '+110–260 ms & 18% loss upstream', icon: Globe, active: 'border-violet-500/60 bg-violet-500/15 text-violet-200' },
  { mode: 'dns_slowdown', label: 'Simulate DNS Slowdown', hint: '+220–900 ms resolver time', icon: Server, active: 'border-yellow-500/60 bg-yellow-500/15 text-yellow-200' },
  { mode: 'bufferbloat', label: 'Simulate Bufferbloat', hint: '~95% link saturation + queueing', icon: Gauge, active: 'border-rose-500/60 bg-rose-500/15 text-rose-200' },
];

export default function ChaosControls({ mode, disabled }: Props) {
  const [pending, setPending] = useState<ChaosMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  const trigger = async (next: ChaosMode) => {
    if (next === mode || pending) return;
    setPending(next);
    setError(null);
    try {
      await api.setChaos(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="panel p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="panel-title">
          <FlaskConical className="h-3.5 w-3.5 text-fuchsia-400" />
          Chaos Simulation
        </h3>
        <p className="text-[11px] text-slate-500">
          Faults are layered on top of real probes before analysis: watch the RCA engine find them.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {OPTIONS.map(({ mode: m, label, hint, icon: Icon, active }) => {
          const isActive = m === mode;
          return (
            <button
              key={m}
              type="button"
              disabled={disabled || pending !== null}
              onClick={() => trigger(m)}
              aria-pressed={isActive}
              className={cx(
                'flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-50',
                isActive ? active : 'border-slate-800 bg-slate-950/50 text-slate-300 hover:border-slate-600 hover:bg-slate-900',
              )}
            >
              {pending === m ? <LoaderCircle className="mt-0.5 h-4 w-4 shrink-0 animate-spin" /> : <Icon className="mt-0.5 h-4 w-4 shrink-0" />}
              <span className="min-w-0">
                <span className="block text-sm font-medium">{label}</span>
                <span className="block text-[11px] text-slate-500">{hint}</span>
              </span>
            </button>
          );
        })}
      </div>
      {error && <p className="mt-2 text-xs text-rose-400">Could not change mode: {error}</p>}
    </section>
  );
}
