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
  /* Adaptive baseline (robust MAD z-score). Optional for backward compatibility. */
  baseline_median_ms?: number | null;
  baseline_sigma_ms?: number | null;
  baseline_samples?: number;
  z_score?: number | null;
  anomaly?: boolean;
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

export type WifiBand = '2.4 GHz' | '5 GHz' | '6 GHz';
export type SignalQuality = 'good' | 'fair' | 'weak';

export interface WifiInfo {
  is_wireless: boolean;
  interface: string | null;
  ssid: string | null;
  rssi_dbm: number | null;
  signal_pct: number | null;
  rssi_estimated: boolean;
  band: WifiBand | null;
  channel: number | null;
  frequency_mhz: number | null;
  radio_type: string | null;
  rx_rate_mbps: number | null;
  tx_rate_mbps: number | null;
  signal_quality: SignalQuality | null;
  source: string;
  error: string | null;
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
  wifi?: WifiInfo | null;
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

export type StressTestState = 'idle' | 'running' | 'completed' | 'failed' | 'aborted';
export type BufferbloatGrade = 'A+' | 'A' | 'B' | 'C' | 'D' | 'F' | '?';

export interface StressTestResult {
  grade: BufferbloatGrade;
  unloaded_gateway_ms: number | null;
  unloaded_wan_ms: number | null;
  loaded_gateway_median_ms: number | null;
  loaded_gateway_p95_ms: number | null;
  loaded_wan_median_ms: number | null;
  loaded_wan_p95_ms: number | null;
  inflation_ms: number | null;
  inflation_multiplier: number | null;
  peak_download_mbps: number;
  peak_upload_mbps: number;
  bytes_downloaded: number;
  bytes_uploaded: number;
  loaded_samples: number;
  lost_samples: number;
  endpoint: string | null;
  recommendation: string | null;
  reference_hop: 'wan' | 'gateway';
}

export interface StressTestStatus {
  state: StressTestState;
  test_id: string | null;
  started_at: number | null;
  finished_at: number | null;
  duration_s: number;
  progress: number;
  live_download_mbps: number;
  live_upload_mbps: number;
  result: StressTestResult | null;
  error: string | null;
  diagnostics?: string[];
}

export type IncidentEvent = 'opened' | 'updated' | 'closed';

export type ServerMessage =
  | { type: 'snapshot'; frames: TelemetryFrame[]; stress_test?: StressTestStatus }
  | { type: 'telemetry'; frame: TelemetryFrame }
  | { type: 'incident'; event: IncidentEvent; incident: Incident }
  | { type: 'chaos'; mode: ChaosMode; since: number | null }
  | { type: 'stress_test'; status: StressTestStatus }
  | { type: 'pong' };

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'offline';
