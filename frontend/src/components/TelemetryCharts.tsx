import { type ReactNode, useMemo } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ArrowDownUp, Timer } from 'lucide-react';
import type { TelemetryFrame } from '../types/telemetry';
import { fmtClock } from '../lib/format';

interface Props {
  history: TelemetryFrame[];
}

interface Point {
  t: string;
  gateway: number | null;
  wan: number | null;
  dns: number | null;
  download: number;
  upload: number;
}

const COLORS = {
  gateway: '#22d3ee', // cyan-400
  wan: '#a78bfa', // violet-400
  dns: '#fbbf24', // amber-400
  download: '#34d399', // emerald-400
  upload: '#38bdf8', // sky-400
  grid: '#1e293b',
  axis: '#64748b',
};

const tooltipStyle = {
  contentStyle: {
    background: 'rgba(2,6,23,0.95)',
    border: '1px solid #334155',
    borderRadius: 8,
    fontSize: 12,
    fontFamily: 'ui-monospace, monospace',
  },
  labelStyle: { color: '#94a3b8' },
  itemStyle: { padding: 0 },
};

const axisProps = {
  stroke: COLORS.axis,
  tick: { fontSize: 11, fill: COLORS.axis },
  tickLine: false,
  axisLine: false,
} as const;

function ChartCard({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="panel p-4">
      <h3 className="panel-title mb-3">
        {icon}
        {title}
      </h3>
      <div className="h-[260px]">{children}</div>
    </div>
  );
}

export default function TelemetryCharts({ history }: Props) {
  const data = useMemo<Point[]>(
    () =>
      history.map((f) => ({
        t: fmtClock(f.timestamp * 1000),
        // Lost probes render as gaps, which is exactly how packet loss should look.
        gateway: f.hops.gateway.current_ms,
        wan: f.hops.wan.current_ms,
        dns: f.hops.dns.current_ms,
        download: Number(f.bandwidth.download_mbps.toFixed(3)),
        upload: Number(f.bandwidth.upload_mbps.toFixed(3)),
      })),
    [history],
  );

  const fmtTip = (unit: string) => (value: unknown) =>
    typeof value === 'number' ? `${value.toFixed(value >= 100 ? 0 : 1)} ${unit}` : String(value ?? '—');

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartCard title="Latency · Gateway vs WAN vs DNS (ms)" icon={<Timer className="h-3.5 w-3.5" />}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} syncId="telemetry" margin={{ top: 6, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="t" {...axisProps} minTickGap={48} />
            <YAxis {...axisProps} width={48} allowDecimals={false} />
            <Tooltip {...tooltipStyle} formatter={fmtTip('ms')} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, paddingTop: 4 }} />
            <ReferenceLine y={150} stroke="#f59e0b" strokeOpacity={0.25} strokeDasharray="4 4" />
            <Line name="Gateway" type="monotone" dataKey="gateway" stroke={COLORS.gateway} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
            <Line name="WAN" type="monotone" dataKey="wan" stroke={COLORS.wan} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
            <Line name="DNS" type="monotone" dataKey="dns" stroke={COLORS.dns} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="Throughput · Download vs Upload (Mbps)" icon={<ArrowDownUp className="h-3.5 w-3.5" />}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} syncId="telemetry" margin={{ top: 6, right: 12, left: -12, bottom: 0 }}>
            <defs>
              <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.download} stopOpacity={0.35} />
                <stop offset="100%" stopColor={COLORS.download} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.upload} stopOpacity={0.3} />
                <stop offset="100%" stopColor={COLORS.upload} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="t" {...axisProps} minTickGap={48} />
            <YAxis {...axisProps} width={48} />
            <Tooltip {...tooltipStyle} formatter={fmtTip('Mbps')} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, paddingTop: 4 }} />
            <Area name="Download" type="monotone" dataKey="download" stroke={COLORS.download} strokeWidth={2} fill="url(#gDown)" isAnimationActive={false} />
            <Area name="Upload" type="monotone" dataKey="upload" stroke={COLORS.upload} strokeWidth={2} fill="url(#gUp)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}
