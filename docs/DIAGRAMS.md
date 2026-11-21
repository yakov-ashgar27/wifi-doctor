# WiFi-Doctor: Data Flow & Data Model

Drawn from the code: the pipeline in [`backend/app/service.py`](../backend/app/service.py), the probes and analysis in [`backend/app/engine/`](../backend/app/engine/) and the ORM model in [`backend/app/models.py`](../backend/app/models.py). GitHub renders the diagrams directly.

**DFD notation:** rectangles are external entities, numbered rounded boxes are processes, cylinders are data stores.

## DFD Level 0: context diagram

```mermaid
flowchart LR
    U["User"]
    NET["Network path<br/>Gateway (Wi-Fi/LAN) · ISP/WAN (1.1.1.1, 8.8.8.8) · DNS"]
    OS["Operating system<br/>NIC counters · processes · Wi-Fi radio"]
    ISP["ISP support"]
    SYS(("0<br/>WiFi-Doctor"))

    SYS -- "ICMP/TCP probes, DNS queries,<br/>load-test traffic" --> NET
    NET -- "RTT, loss, DNS time" --> SYS
    OS -- "throughput, top process,<br/>RSSI, band, channel" --> SYS
    U -- "start load test, chaos mode, filters" --> SYS
    SYS -- "live diagnosis, confidence, fix,<br/>incident history" --> U
    SYS -. "CSV incident report<br/>(sent by the user)" .-> ISP
```

## DFD Level 1

```mermaid
flowchart TB
    U["User"]
    NET["Network path"]
    OS["Operating system"]

    P1("1.0<br/>Probe each layer<br/>every 1.5 s")
    P2("2.0<br/>Monitor host<br/>bandwidth · Wi-Fi radio")
    P3("3.0<br/>Analyze & diagnose<br/>RCA engine")
    P4("4.0<br/>Record incidents")
    P5("5.0<br/>Bufferbloat load test<br/>8 s, graded A+ to F")
    P6("6.0<br/>Report & export")

    D1[("D1 Rolling windows<br/>60 samples per hop + robust baseline<br/>(in memory)")]
    D2[("D2 incident_logs<br/>(SQLite)")]

    NET -- "RTT, loss, DNS time" --> P1
    P1 -- "probe results" --> D1
    OS -- "NIC counters, processes, RSSI/channel" --> P2
    P2 -- "throughput, utilisation, Wi-Fi info" --> P3
    D1 -- "avg / P95 RTT, jitter, loss, z-score" --> P3
    P3 -- "verdict: LOCAL_WIFI · BUFFERBLOAT ·<br/>ISP_WAN · DNS_SERVER · HEALTHY" --> P4
    P4 -- "open / resolve incident" --> D2
    P3 -- "WebSocket: live telemetry + diagnosis" --> U
    U -- "start test" --> P5
    D1 -- "unloaded baseline" --> P5
    P5 -- "saturating download/upload" --> NET
    P5 -- "grade, latency under load" --> U
    U -- "filters" --> P6
    D2 -- "incidents" --> P6
    P6 -- "paged history, CSV file" --> U
```

## ERD

WiFi-Doctor keeps live measurements in memory and stores only one kind of record: an **incident**, from the moment a problem is detected until it resolves. It is a single, deliberately denormalized table, so each row is a self-contained piece of evidence (the snapshot of every layer at the time) that can be exported to CSV and sent to an ISP as is.

```mermaid
erDiagram
    INCIDENT_LOGS {
        int id PK
        datetime started_at "indexed"
        datetime resolved_at "nullable"
        float duration_s "nullable"
        string severity "WARNING / CRITICAL (peak)"
        string root_cause_layer "indexed"
        int confidence_score
        string title
        text explanation
        text recommended_action
        string gateway_ip
        string wan_target
        string dns_server
        string probe_backend
        float gateway_rtt_ms
        float gateway_loss_pct
        float wan_rtt_ms
        float wan_jitter_ms
        float wan_loss_pct
        float dns_ms
        float dns_loss_pct
        float download_mbps
        float upload_mbps
        string top_process
        bool simulated "indexed, chaos-mode incidents"
        string chaos_mode
    }
```
