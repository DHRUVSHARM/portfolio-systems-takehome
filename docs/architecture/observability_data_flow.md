# Observability Data Flow

```mermaid
flowchart LR
  subgraph Services
    gateway[Gateway]
    portfolio[Portfolio API and agents]
    vllm[vLLM]
    exporters[node-exporter / cAdvisor / DCGM]
  end
  gateway --> prometheus[Prometheus]
  portfolio --> prometheus
  vllm --> prometheus
  exporters --> prometheus
  prometheus --> grafana[Grafana live dashboards]

  gateway --> otel[OpenTelemetry Collector]
  portfolio --> otel
  otel --> jaeger[Jaeger]
  jaeger --> grafana

  gateway --> alloy[Grafana Alloy]
  portfolio --> alloy
  alloy --> loki[Loki]
  loki --> explore[Grafana Explore]
```

Prometheus labels stay low-cardinality. Request identity belongs in trace
attributes, structured JSON log fields, benchmark artifacts, PostgreSQL, and
Parquet.

Use these live views:

- Grafana `System Overview`: service health and high-level load.
- Grafana `Gateway`: admission, queueing, rejections, downstream latency.
- Grafana `Agent / Workflow Breakdown`: agent/tool volume and latency.
- Grafana `vLLM Inference`: scheduler, token, queue, KV-cache, and inference metrics.
- Grafana `Resources`: host/container/GPU resource telemetry.
- Grafana `Live Benchmark`: live traffic during an experiment.
- Jaeger: exact request trace hierarchy.
- Grafana Explore with Loki: correlated JSON logs.
