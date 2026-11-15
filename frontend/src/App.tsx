import Header from './components/Header';
import VerdictBanner from './components/VerdictBanner';
import TopologyMap from './components/TopologyMap';
import TelemetryCharts from './components/TelemetryCharts';
import ChaosControls from './components/ChaosControls';
import IncidentTable from './components/IncidentTable';
import { useTelemetry } from './hooks/useTelemetry';

export default function App() {
  const t = useTelemetry();

  return (
    <div className="min-h-screen">
      <Header connection={t.connection} reconnectAttempt={t.reconnectAttempt} latest={t.latest} onReconnect={t.reconnectNow} />
      <main className="mx-auto flex max-w-[1500px] flex-col gap-4 px-4 py-5 sm:px-6">
        <VerdictBanner frame={t.latest} />
        <ChaosControls mode={t.chaosMode} disabled={t.connection !== 'open'} />
        <TopologyMap frame={t.latest} />
        <TelemetryCharts history={t.history} />
        <IncidentTable refreshKey={t.incidentVersion} />
        <footer className="pb-4 pt-2 text-center text-[11px] text-slate-600">
          WiFi-Doctor · probes every 1.5 s · 100% local, no data leaves this machine
        </footer>
      </main>
    </div>
  );
}
