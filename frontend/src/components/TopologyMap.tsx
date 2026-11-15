import { type ReactNode, useState } from 'react';
import { Globe, Laptop, type LucideIcon, Router, Server, Wifi, X } from 'lucide-react';
import type { HopId, HopStats, HopStatus, RootCauseLayer, TelemetryFrame, WifiInfo } from '../types/telemetry';
import { STATUS_TONE, cx, fmtMbps, fmtMs, fmtPct } from '../lib/format';

type NodeId = 'device' | HopId;

interface Props {
  frame: TelemetryFrame | null;
}

const ROOT_NODE: Record<RootCauseLayer, NodeId | null> = {
  NONE: null,
  LOCAL_WIFI: 'gateway',
  BUFFERBLOAT: 'device',
  ISP_WAN: 'wan',
  DNS_SERVER: 'dns',
};

const ICONS: Record<NodeId, LucideIcon> = { device: Laptop, gateway: Router, wan: Globe, dns: Server };
const TITLES: Record<NodeId, string> = {
  device: 'This Device',
  gateway: 'Local Router / Gateway',
  wan: 'ISP / External WAN',
  dns: 'DNS Resolver',
};

function Metric({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={cx('num truncate text-sm text-slate-200', tone)}>
        {value}
        {unit && value !== '—' && <span className="ml-0.5 text-[10px] text-slate-500">{unit}</span>}
      </div>
    </div>
  );
}

function Connector({ status, active }: { status: HopStatus; active: boolean }) {
  const tone = STATUS_TONE[status];
  const speed = status === 'CRITICAL' ? 'animate-flow-slow' : 'animate-flow';
  return (
    <div className="flex items-center justify-center lg:flex-1" aria-hidden>
      {/* horizontal on desktop */}
      <div className={cx('hidden h-0.5 w-full lg:block', tone.line, 'flow-line', active && speed)} />
      {/* vertical on mobile */}
      <div className={cx('h-6 w-0.5 lg:hidden', tone.line, 'flow-line-v')} />
    </div>
  );
}

const SIGNAL_TONE = {
  good: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  fair: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  weak: 'border-rose-500/50 bg-rose-500/10 text-rose-300',
} as const;

function WifiBadge({ wifi }: { wifi: WifiInfo | null | undefined }) {
  if (!wifi) return null;
  if (!wifi.is_wireless) {
    if (wifi.error === 'location-permission-required') {
      return (
        <span className="rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-400" title="Windows needs Location services enabled to read Wi-Fi details">
          Wi-Fi details: enable Location
        </span>
      );
    }
    return wifi.source === 'none' ? null : (
      <span className="rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-400">Wired / no Wi-Fi</span>
    );
  }
  const tone = wifi.signal_quality ? SIGNAL_TONE[wifi.signal_quality] : 'border-slate-700 bg-slate-800/60 text-slate-300';
  const parts = [wifi.band, wifi.channel !== null ? `ch ${wifi.channel}` : null].filter(Boolean).join(' · ');
  return (
    <span
      className={cx('num inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[10px]', tone)}
      title={`${wifi.ssid ?? 'Wi-Fi'}${wifi.radio_type ? ` · ${wifi.radio_type}` : ''}${wifi.rssi_estimated ? ' · dBm estimated from signal %' : ''}${wifi.simulated ? ' · simulated' : ''}`}
    >
      <Wifi className="h-3 w-3" />
      {parts && <span>{parts}</span>}
      {wifi.rssi_dbm !== null && (
        <span className="font-semibold">
          {wifi.rssi_estimated ? '≈' : ''}
          {wifi.rssi_dbm} dBm
        </span>
      )}
    </span>
  );
}

function BaselineLine({ hop }: { hop: HopStats }) {
  if (hop.baseline_median_ms === null || hop.baseline_median_ms === undefined) return null;
  const ready = (hop.baseline_samples ?? 0) >= 60;
  const z = hop.z_score;
  return (
    <div
      className={cx('num mt-2 truncate text-[10px]', hop.anomaly ? 'text-amber-300' : 'text-slate-500')}
      title={ready ? 'Robust z-score vs. this network\'s learned baseline (median / MAD)' : `Learning baseline: ${hop.baseline_samples}/60 samples`}
    >
      {ready ? 'Normal baseline' : 'Learning'}: {fmtMs(hop.baseline_median_ms)} ms
      {ready && z !== null && z !== undefined && ` (Z: ${z >= 0 ? '+' : ''}${z.toFixed(1)})`}
      {hop.anomaly && ' · anomaly'}
    </div>
  );
}

interface NodeProps {
  id: NodeId;
  status: HopStatus;
  address: string | null | undefined;
  isRoot: boolean;
  selected: boolean;
  onSelect: (id: NodeId) => void;
  children: ReactNode;
  footer?: ReactNode;
}

function Node({ id, status, address, isRoot, selected, onSelect, children, footer }: NodeProps) {
  const tone = STATUS_TONE[status];
  const Icon = ICONS[id];
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      aria-pressed={selected}
      className={cx(
        'group relative w-full rounded-xl border bg-slate-950/70 p-4 text-left transition duration-500 lg:w-[230px] lg:shrink-0',
        tone.border,
        tone.glow,
        selected ? 'ring-2 ring-cyan-400/60' : 'hover:border-slate-500',
      )}
    >
      {isRoot && (
        <span className="absolute -top-2.5 left-3 rounded bg-rose-500 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-white shadow">
          root cause
        </span>
      )}
      <div className="mb-3 flex items-center gap-3">
        <div className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-lg border', tone.border, tone.bg, tone.text)}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-100">{TITLES[id]}</div>
          <div className="num truncate text-xs text-slate-400">{address ?? 'not detected'}</div>
        </div>
        <span className={cx('h-2.5 w-2.5 shrink-0 rounded-full', tone.dot, status !== 'HEALTHY' && status !== 'UNKNOWN' && 'animate-pulse')} />
      </div>
      <div className="grid grid-cols-3 gap-2">{children}</div>
      {footer}
    </button>
  );
}

function HopMetrics({ hop }: { hop: HopStats }) {
  const tone = STATUS_TONE[hop.status];
  return (
    <>
      <Metric label={hop.hop === 'dns' ? 'Resolve' : 'RTT'} value={fmtMs(hop.recent_ms)} unit="ms" tone={hop.status !== 'HEALTHY' ? tone.text : undefined} />
      <Metric label="Jitter" value={fmtMs(hop.jitter_ms)} unit="ms" />
      <Metric label="Loss" value={fmtPct(hop.loss_pct)} tone={hop.loss_pct > 0 ? 'text-amber-300' : undefined} />
    </>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/50 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="num text-sm text-slate-200">{value}</div>
    </div>
  );
}

export default function TopologyMap({ frame }: Props) {
  const [selected, setSelected] = useState<NodeId | null>(null);
  if (!frame) return <section className="panel h-[220px] animate-pulse" />;

  const { hops, bandwidth: bw, network: net, diagnosis } = frame;
  const rootNode = diagnosis.status === 'HEALTHY' ? null : ROOT_NODE[diagnosis.root_cause_layer];
  const deviceStatus: HopStatus = rootNode === 'device' ? diagnosis.status : 'HEALTHY';
  const toggle = (id: NodeId) => setSelected((cur) => (cur === id ? null : id));
  const selectedHop = selected && selected !== 'device' ? hops[selected] : null;

  return (
    <section className="panel p-5">
      <div className="mb-5 flex items-center justify-between">
        <h3 className="panel-title">Network Path Topology</h3>
        <span className="text-[11px] text-slate-500">Click a node for full-window statistics</span>
      </div>

      <div className="flex flex-col items-stretch lg:flex-row lg:items-center">
        <Node id="device" status={deviceStatus} address={net.local_ip} isRoot={rootNode === 'device'} selected={selected === 'device'} onSelect={toggle}>
          <Metric label="Down" value={fmtMbps(bw.download_mbps)} unit="Mb/s" />
          <Metric label="Up" value={fmtMbps(bw.upload_mbps)} unit="Mb/s" />
          <Metric label="Util" value={fmtPct(bw.utilization_pct)} tone={bw.utilization_pct > 70 ? 'text-amber-300' : undefined} />
        </Node>
        <Connector status={hops.gateway.status === 'UNKNOWN' ? 'HEALTHY' : hops.gateway.status} active />
        {(['gateway', 'wan', 'dns'] as const).map((id, i, arr) => (
          <div key={id} className="contents">
            <Node
              id={id}
              status={hops[id].status}
              address={hops[id].target}
              isRoot={rootNode === id}
              selected={selected === id}
              onSelect={toggle}
              footer={
                <>
                  <BaselineLine hop={hops[id]} />
                  {id === 'gateway' && frame.wifi && (
                    <div className="mt-2">
                      <WifiBadge wifi={frame.wifi} />
                    </div>
                  )}
                </>
              }
            >
              <HopMetrics hop={hops[id]} />
            </Node>
            {i < arr.length - 1 && <Connector status={hops[arr[i + 1]].status} active />}
          </div>
        ))}
      </div>

      {selected && (
        <div className="mt-5 rounded-lg border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-200">
              {TITLES[selected]} <span className="ml-2 text-xs font-normal text-slate-500">rolling 60-sample window</span>
            </p>
            <button type="button" onClick={() => setSelected(null)} className="text-slate-500 hover:text-slate-300" aria-label="Close details">
              <X className="h-4 w-4" />
            </button>
          </div>
          {selectedHop ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
              <Detail label="Target" value={selectedHop.target ?? '—'} />
              <Detail label="Current" value={`${fmtMs(selectedHop.current_ms, 'timeout')} ms`} />
              <Detail label="Average" value={`${fmtMs(selectedHop.avg_ms)} ms`} />
              <Detail label="P95" value={`${fmtMs(selectedHop.p95_ms)} ms`} />
              <Detail label="Min / Max" value={`${fmtMs(selectedHop.min_ms)} / ${fmtMs(selectedHop.max_ms)}`} />
              <Detail label="Jitter" value={`${fmtMs(selectedHop.jitter_ms)} ms`} />
              <Detail label="Loss (30s / 90s)" value={`${fmtPct(selectedHop.recent_loss_pct)} / ${fmtPct(selectedHop.loss_pct)}`} />
              <Detail label="Samples · method" value={`${selectedHop.samples} · ${selectedHop.method}`} />
              <Detail
                label="Learned baseline"
                value={selectedHop.baseline_median_ms != null ? `${fmtMs(selectedHop.baseline_median_ms)} ms ± ${fmtMs(selectedHop.baseline_sigma_ms)}` : '—'}
              />
              <Detail label="Robust z-score" value={selectedHop.z_score != null ? `${selectedHop.z_score >= 0 ? '+' : ''}${selectedHop.z_score.toFixed(1)}` : '—'} />
              <Detail label="Baseline samples" value={`${selectedHop.baseline_samples ?? 0} / 60 min`} />
              <Detail label="Anomaly" value={selectedHop.anomaly ? 'yes (z > 3.5)' : 'no'} />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
              <Detail label="Interface" value={net.interface ?? '—'} />
              <Detail label="Medium" value={bw.is_wireless === null ? 'unknown' : bw.is_wireless ? 'Wi-Fi' : 'Wired'} />
              <Detail label="Link speed" value={bw.link_speed_mbps ? `${bw.link_speed_mbps} Mb/s` : '—'} />
              <Detail label="Peak down" value={`${fmtMbps(bw.peak_download_mbps)} Mb/s`} />
              <Detail label="Peak up" value={`${fmtMbps(bw.peak_upload_mbps)} Mb/s`} />
              <Detail label="Top process" value={bw.top_process?.name ?? '—'} />
              <Detail label="Connections" value={bw.top_process ? String(bw.top_process.connections) : '—'} />
              <Detail label="Platform" value={net.platform} />
              <Detail label="SSID" value={frame.wifi?.ssid ?? '—'} />
              <Detail
                label="Signal"
                value={frame.wifi?.rssi_dbm != null ? `${frame.wifi.rssi_estimated ? '≈' : ''}${frame.wifi.rssi_dbm} dBm${frame.wifi.signal_pct != null ? ` (${frame.wifi.signal_pct}%)` : ''}` : '—'}
              />
              <Detail label="Band · channel" value={frame.wifi?.band ? `${frame.wifi.band} · ${frame.wifi.channel ?? '?'}` : '—'} />
              <Detail
                label="PHY rate rx / tx"
                value={frame.wifi?.rx_rate_mbps != null || frame.wifi?.tx_rate_mbps != null ? `${frame.wifi?.rx_rate_mbps ?? '—'} / ${frame.wifi?.tx_rate_mbps ?? '—'} Mb/s` : '—'}
              />
            </div>
          )}
        </div>
      )}
    </section>
  );
}
