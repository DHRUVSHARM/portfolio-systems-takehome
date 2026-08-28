# Analytics Data Flow

```mermaid
flowchart TD
  benchmark[Benchmark artifacts] --> collector[Analytics collector]
  jaeger[Jaeger trace export] --> collector
  prometheus[Prometheus range export] --> collector
  provenance[Provenance and config snapshot] --> collector
  cost[Versioned cost profile] --> collector
  collector --> postgres[PostgreSQL historical analytics]
  collector --> parquet[Parquet run artifacts]
  collector --> json[metrics.json and report.json]
  collector --> chartdata[charts/*.json]
  collector --> html[report/index.html and SVG charts]
```

Raw observations are persisted first:

- `requests.parquet`
- `execution_observations.parquet`
- `inference_observations.parquet`
- `resource_samples.parquet`

Derived outputs are recalculable:

- `request_cost_attributions.parquet`
- `agent_cost_attributions.parquet`
- `metrics.json`
- `report.json`
- `charts/*.json`
- `serving_telemetry.json`
- `report/index.html`

Historical reproducibility does not depend on Jaeger retaining traces forever;
the exported trace content is normalized into execution and inference
observations and preserved in raw artifact fields.
