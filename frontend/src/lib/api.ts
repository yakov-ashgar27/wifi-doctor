import type { ChaosMode, ChaosState, IncidentPage, StressTestStatus } from '../types/telemetry';

export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ''}`);
  }
  return (await res.json()) as T;
}

export const api = {
  incidents: (page: number, pageSize: number, realOnly: boolean) =>
    request<IncidentPage>(`/api/incidents?page=${page}&page_size=${pageSize}&real_only=${realOnly}`),
  setChaos: (mode: ChaosMode) => request<ChaosState>(`/api/chaos/${mode}`, { method: 'POST' }),
  startStressTest: () => request<StressTestStatus>('/api/stress-test/start', { method: 'POST' }),
  stressTestStatus: () => request<StressTestStatus>('/api/stress-test/status'),
  exportUrl: (realOnly: boolean) => `${API_BASE}/api/incidents/export?real_only=${realOnly}`,
};
