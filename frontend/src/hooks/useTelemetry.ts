import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  ChaosMode,
  ConnectionState,
  Incident,
  IncidentEvent,
  ServerMessage,
  StressTestStatus,
  TelemetryFrame,
} from '../types/telemetry';

const HISTORY_LIMIT = 80; // 80 x 1.5 s = 2 minutes of chart history
const STALE_AFTER_MS = 6000; // server ticks every 1.5 s; silence => dead socket
const BASE_BACKOFF_MS = 800;
const MAX_BACKOFF_MS = 10000;

export function resolveWsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL;
  if (configured) return configured;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws`;
}

export interface LastIncidentEvent {
  event: IncidentEvent;
  incident: Incident;
  receivedAt: number;
}

export interface TelemetryState {
  latest: TelemetryFrame | null;
  history: TelemetryFrame[];
  connection: ConnectionState;
  reconnectAttempt: number;
  lastMessageAt: number | null;
  chaosMode: ChaosMode;
  incidentVersion: number;
  lastIncident: LastIncidentEvent | null;
  stressTest: StressTestStatus | null;
  reconnectNow: () => void;
}

/**
 * Resilient telemetry stream:
 *  - exponential backoff with jitter on disconnect
 *  - watchdog that recycles a socket that went silent (half-open TCP)
 *  - bounded ring buffer for chart history, primed by the server snapshot
 *  - reconnects immediately when the tab becomes visible / network returns
 */
export function useTelemetry(): TelemetryState {
  const [latest, setLatest] = useState<TelemetryFrame | null>(null);
  const [history, setHistory] = useState<TelemetryFrame[]>([]);
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const [chaosMode, setChaosMode] = useState<ChaosMode>('none');
  const [incidentVersion, setIncidentVersion] = useState(0);
  const [lastIncident, setLastIncident] = useState<LastIncidentEvent | null>(null);
  const [stressTest, setStressTest] = useState<StressTestStatus | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const retryTimer = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const lastMsgRef = useRef<number>(0);
  const disposed = useRef(false);

  const handleMessage = useCallback((raw: string) => {
    let msg: ServerMessage;
    try {
      msg = JSON.parse(raw) as ServerMessage;
    } catch {
      return;
    }
    const now = Date.now();
    lastMsgRef.current = now;
    setLastMessageAt(now);

    switch (msg.type) {
      case 'snapshot': {
        const frames = msg.frames.slice(-HISTORY_LIMIT);
        setHistory(frames);
        if (frames.length) {
          const last = frames[frames.length - 1];
          setLatest(last);
          setChaosMode(last.chaos_mode);
        }
        if (msg.stress_test) setStressTest(msg.stress_test);
        break;
      }
      case 'telemetry':
        setLatest(msg.frame);
        setChaosMode(msg.frame.chaos_mode);
        setHistory((prev) => {
          if (prev.length && prev[prev.length - 1].tick >= msg.frame.tick) return prev; // duplicate
          const next = prev.length >= HISTORY_LIMIT ? prev.slice(prev.length - HISTORY_LIMIT + 1) : prev.slice();
          next.push(msg.frame);
          return next;
        });
        break;
      case 'incident':
        setIncidentVersion((v) => v + 1);
        setLastIncident({ event: msg.event, incident: msg.incident, receivedAt: now });
        break;
      case 'chaos':
        setChaosMode(msg.mode);
        break;
      case 'stress_test':
        setStressTest(msg.status);
        break;
      default:
        break;
    }
  }, []);

  const connectRef = useRef<() => void>(() => undefined);

  const scheduleReconnect = useCallback(() => {
    if (disposed.current || retryTimer.current !== null) return;
    attemptRef.current += 1;
    setReconnectAttempt(attemptRef.current);
    setConnection(navigator.onLine === false ? 'offline' : 'reconnecting');
    const exp = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** (attemptRef.current - 1));
    const delay = exp / 2 + Math.random() * (exp / 2); // "equal jitter"
    retryTimer.current = window.setTimeout(() => {
      retryTimer.current = null;
      connectRef.current();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    if (disposed.current) return;
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    const existing = wsRef.current;
    if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
      return;
    }

    setConnection(attemptRef.current === 0 ? 'connecting' : 'reconnecting');
    let ws: WebSocket;
    try {
      ws = new WebSocket(resolveWsUrl());
    } catch {
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      setReconnectAttempt(0);
      lastMsgRef.current = Date.now();
      setConnection('open');
    };
    ws.onmessage = (ev: MessageEvent<string>) => handleMessage(ev.data);
    ws.onerror = () => {
      // onclose follows; reconnection is handled there.
    };
    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
      if (!disposed.current) scheduleReconnect();
    };
  }, [handleMessage, scheduleReconnect]);
  connectRef.current = connect;

  const reconnectNow = useCallback(() => {
    attemptRef.current = 0;
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) return;
    if (ws) {
      ws.onclose = null;
      ws.close();
      wsRef.current = null;
    }
    connect();
  }, [connect]);

  useEffect(() => {
    disposed.current = false;
    connect();

    // Watchdog: a socket that is "open" but silent is dead (sleep/wake, NAT timeout...).
    const watchdog = window.setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMsgRef.current > STALE_AFTER_MS) {
        ws.close();
      }
    }, 2000);

    const onVisible = () => {
      if (document.visibilityState === 'visible') reconnectNow();
    };
    const onOnline = () => reconnectNow();
    const onOffline = () => setConnection('offline');
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);

    return () => {
      disposed.current = true;
      window.clearInterval(watchdog);
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      wsRef.current = null;
    };
  }, [connect, reconnectNow]);

  return {
    latest,
    history,
    connection,
    reconnectAttempt,
    lastMessageAt,
    chaosMode,
    incidentVersion,
    lastIncident,
    stressTest,
    reconnectNow,
  };
}
