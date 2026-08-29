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

## Drilldown And Identity Flow

```mermaid
flowchart LR
  run[run_id] --> request[request_id]
  request --> query[query_id]
  query --> trace[trace/span]
  trace --> agent[agent/tool observation]
  agent --> inference[inference observation]
  inference --> postgres[PostgreSQL]
  inference --> parquet[Parquet artifacts]
  agent --> postgres
  agent --> parquet
```

The same identity values are present in traces, logs, benchmark observations,
PostgreSQL rows, and Parquet files. They are intentionally absent from
Prometheus labels.

## Cost Attribution Flow

```mermaid
flowchart TD
  hourly[machine hourly rate] --> total[total run cost]
  duration[measured run duration] --> total
  total --> cpu[CPU pool]
  total --> gpu[inference-GPU pool]
  total --> overhead[overhead_unallocated]
  cpu --> request[request attribution]
  gpu --> request
  overhead --> request
  cpu --> agent[agent attribution]
  gpu --> agent
  overhead --> agent
  request --> metrics[assignment metrics]
  agent --> metrics
```

The `overhead_unallocated` pool remains explicit so attributed request costs
sum back to the run cost without pretending all shared infrastructure time came
from one agent.
