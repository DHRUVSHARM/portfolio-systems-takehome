# Data Location Matrix

Companion docs:
[experiment results](experiment_results.md),
[metrics reference](metrics_reference.md), and
[tradeoffs/future work](tradeoffs_and_future_work.md).

| Question | Live location | Historical location | Raw artifact |
| --- | --- | --- | --- |
| Current request rate | Grafana -> System Overview / Live Benchmark | PostgreSQL `requests` grouped by time | `requests.parquet` |
| Gateway p95 latency | Grafana -> Gateway | `requests.client_latency_ms`, derived metrics | `requests.parquet`, `metrics.json` |
| Gateway queueing | Grafana -> Gateway | `requests.gateway_queue_wait_ms` when present | `requests.parquet` |
| Gateway errors | Grafana -> Gateway | PostgreSQL `requests.success/error_type` | `requests.parquet` |
| Agent call rate | Grafana -> Agent / Workflow Breakdown | PostgreSQL `execution_observations` | `execution_observations.parquet` |
| Agent p95 latency | Grafana -> Agent / Workflow Breakdown | PostgreSQL `agent_cost_breakdown` | `agent_cost_attributions.parquet` |
| Tool latency | Grafana -> Agent / Workflow Breakdown | Historical Analytics `Execution Observations` | `execution_observations.parquet` |
| Workflow fan-out | Grafana -> Agent / Workflow Breakdown | `report/index.html` -> Agents & Tools | `charts/fanout_work.json` |
| vLLM requests running | Grafana -> vLLM Inference | `serving_telemetry.json` run-level vLLM | `resource_samples.parquet` |
| vLLM requests waiting | Grafana -> vLLM Inference | `serving_telemetry.json` run-level vLLM | `resource_samples.parquet` |
| TTFT | Grafana -> vLLM Inference | `serving_telemetry.json` aggregate TTFT | `resource_samples.parquet` |
| TPOT | Grafana -> vLLM Inference | `serving_telemetry.json` aggregate TPOT | `resource_samples.parquet` |
| Token throughput | Grafana -> vLLM Inference | `serving_telemetry.json`, `inference_observations` | `inference_observations.parquet` |
| KV-cache pressure | Grafana -> vLLM Inference | `serving_telemetry.json` | `resource_samples.parquet` |
| GPU utilization | Grafana -> Resources / vLLM Inference | `serving_telemetry.json` GPU section | `resource_samples.parquet` |
| GPU VRAM | Grafana -> Resources | `serving_telemetry.json` GPU section | `resource_samples.parquet` |
| GPU power | Grafana -> Resources | `serving_telemetry.json` GPU section | `resource_samples.parquet` |
| Exact request trace | Jaeger UI | Historical Analytics `Execution Observations` trace IDs | exported Jaeger JSON, `execution_observations.parquet` |
| Exact agent span | Jaeger UI | PostgreSQL `execution_observations` | `execution_observations.parquet` |
| Exact tool execution | Jaeger UI | Historical Analytics `Execution Observations` | `execution_observations.parquet` |
| Exact inference call | Jaeger UI | PostgreSQL `inference_observations` | `inference_observations.parquet` |
| Logs for request | Grafana Explore -> Loki | Retained Loki logs while available | container stdout, trace IDs in JSON logs |
| Total experiment cost | Live not applicable | HTML report -> Assignment Metrics; PostgreSQL `cost_analyses` | `metrics.json`, `report.json` |
| Cost/query | Live not applicable | HTML report; PostgreSQL `request_cost_attributions` | `request_cost_attributions.parquet` |
| Cost/query distribution | Live not applicable | HTML report -> Cost; Historical Analytics | `charts/cost_query_*`, `metrics.json` |
| Percentage cost per agent | Live not applicable | HTML report -> Assignment Metrics; `agent_cost_breakdown` | `agent_cost_attributions.parquet` |
| Query cost | Live not applicable | Historical Analytics `Cost / Query Distribution` | `request_cost_attributions.parquet` |
| Token counts | Jaeger inference span | PostgreSQL `inference_observations` | `inference_observations.parquet` |
| Historical request details | Live not applicable | Historical Analytics `Selected Request Summary` | `requests.parquet` |
| Experiment serving config | Grafana dashboard labels where available | `run.json`, `provenance.json`, HTML report | `provenance.json` |
| Run provenance | Live not applicable | HTML report -> Provenance | `provenance.json`, `run.json` |
| Machine/hardware profile | Grafana -> Resources | HTML report -> Provenance | `provenance.json`, `resource_samples.parquet` |

Successful CPU demo artifacts use:

```text
results/phase8/raw/<RUN_ID>/
results/phase8/telemetry/<RUN_ID>/
results/phase8/analytics/<RUN_ID>/
```

`requests.jsonl` contains the actual API responses, including generated
Advisor summaries. The CPU demo may show GPU/DCGM rows as unavailable; that is
expected and is distinct from a measured zero.
