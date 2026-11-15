/* Strict TypeScript mirrors of the backend payloads (app/engine/analyzer.py, app/models.py). */

export type HealthStatus = 'HEALTHY' | 'WARNING' | 'CRITICAL';
export type HopStatus = HealthStatus | 'UNKNOWN';
export type RootCauseLayer = 'NONE' | 'LOCAL_WIFI' | 'ISP_WAN' | 'DNS_SERVER' | 'BUFFERBLOAT';
export type ChaosMode = 'none' | 'wifi_lag' | 'isp_loss' | 'dns_slowdown' | 'bufferbloat';
export type HopId = 'gateway' | 'wan' | 'dns';
export type ProbeBackend = 'scapy' | 'icmp_dgram' | 'subprocess' | 'tcp' | 'detecting';

export interface HopStats {
  hop: HopId;
  label: string;
  target: string | null;
  method: string;
  current_ms: number | null;
  avg_ms: number | null;
  p95_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  jitter_ms: number | null;
  loss_pct: number;
  recent_ms: number | null;
  recent_loss_pct: number;
  samples: number;
  last_ok: boolean;
  status: HopStatus;
  simulated: boolean;
}

export interface TopProcess {
  name: string;
  pid: number;
  connections: number;
  io_mbps: number | null;
}

export interface BandwidthStats {
  download_mbps: number;
  upload_mbps: number;
  peak_download_mbps: number;
  peak_upload_mbps: number;
  utilization_pct: number;
  interface: string | null;
  link_speed_mbps: number | null;
  is_wireless: boolean | null;
  top_process: TopProcess | null;
  simulated: boolean;
}

export interface NetworkInfo {
  hostname: string;
  platform: string;
  interface: string | null;
  local_ip: string | null;
  gateway_ip: string | null;
  dns_server: string | null;
  probe_backend: ProbeBackend | string;
}

export interface Diagnosis {
  status: HealthStatus;
  root_cause_layer: RootCauseLayer;
  confidence_score: number;
  title: string;
  explanation: string;
  recommended_action: string;
  evidence: Record<string, number | string | null>;
}

export interface TelemetryFrame {
  tick: number;
  timestamp: number; // epoch seconds
  chaos_mode: ChaosMode;
  network: NetworkInfo;
  hops: Record<HopId, HopStats>;
  bandwidth: BandwidthStats;
  diagnosis: Diagnosis;
}

export interface Incident {
  id: number;
  started_at: string;
  resolved_at: string | null;
  duration_s: number | null;
  severity: Exclude<HealthStatus, 'HEALTHY'>;
  root_cause_layer: RootCauseLayer;
  confidence_score: number;
  title: string;
  explanation: string;
  recommended_action: string;
  gateway_ip: string | null;
  wan_target: string | null;
  dns_server: string | null;
  probe_backend: string | null;
  gateway_rtt_ms: number | null;
  gateway_loss_pct: number | null;
  wan_rtt_ms: number | null;
  wan_jitter_ms: number | null;
  wan_loss_pct: number | null;
  dns_ms: number | null;
  dns_loss_pct: number | null;
  download_mbps: number | null;
  upload_mbps: number | null;
  top_process: string | null;
  simulated: boolean;
  chaos_mode: ChaosMode;
}

export interface IncidentPage {
  items: Incident[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface ChaosState {
  mode: ChaosMode;
  since: number | null;
  available: ChaosMode[];
}

export type IncidentEvent = 'opened' | 'updated' | 'closed';

export type ServerMessage =
  | { type: 'snapshot'; frames: TelemetryFrame[] }
  | { type: 'telemetry'; frame: TelemetryFrame }
  | { type: 'incident'; event: IncidentEvent; incident: Incident }
  | { type: 'chaos'; mode: ChaosMode; since: number | null }
  | { type: 'pong' };

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'offline';
