# Metrics Reference

This reference explains what the system measures, why each metric is useful,
where it appears live, and where it is stored historically.

## Benchmark / Request Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| Total queries | Attempted benchmark requests | benchmark runner | run | Live Benchmark | `requests`, `requests.parquet`, `report.json` | Defines workload size, including failures. |
| Success count | Requests with successful business response | benchmark runner | run | Live Benchmark / Gateway | `requests.success`, `report.json` | Reveals correctness and service reliability. |
| Failure count / error rate | Requests that failed or timed out | Gateway and benchmark | run | Gateway | `requests.error_type`, `requests.parquet` | Shows visible service failures; failed attempts still receive cost attribution. |
| Success rate | Successful / attempted requests | derived analytics | run | Live Benchmark | `report.json`, `metrics.json` | A high latency run is still not valid evidence if success rate collapses. |
| Client latency | End-to-end request time observed by benchmark client | benchmark runner | request/run | Live Benchmark | `requests.client_latency_ms`, `requests.parquet` | User-visible latency, including Gateway, workflow, and inference. |
| p50 / p95 / p99 latency | Distribution of client latency | benchmark + analytics | run | Live Benchmark | `charts/latency_distribution.json`, report | Tail latency exposes stalls that averages hide. |
| Completed requests over time | Traffic completion shape during a run | Prometheus/Gateway/benchmark | run | Live Benchmark | raw observations and request timestamps | Useful for seeing pauses, waves, or saturation. |

Latency distribution matters because two runs with the same mean can feel very
different: one may be consistent while another has a long tail.

## Gateway / Admission Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| Validation latency | Time spent validating public request shape | Gateway | request/aggregate | Gateway | traces/logs where retained | Separates malformed-input handling from downstream execution. |
| Admission wait | Time waiting for Gateway in-flight capacity | Gateway | request/aggregate | Gateway | `requests.gateway_queue_wait_ms` when present | Indicates overload before Portfolio/vLLM execution begins. |
| Request count | Requests admitted/handled by Gateway | Gateway metrics | aggregate | Gateway | `requests.parquet` | Establishes observed load. |
| Error count/rate | Gateway 4xx/5xx outcomes | Gateway metrics | aggregate | Gateway | `requests.http_status`, `requests.error_type` | Distinguishes validation, saturation, timeout, and downstream failures. |
| Queue depth / in-flight | Active and waiting admission slots | Gateway metrics | aggregate | Gateway | live aggregate only unless captured in telemetry export | Persistent queueing shows load exceeding immediate service capacity. |

Gateway metrics are useful because they isolate upstream request-management
delay from model-serving and workflow delay.

## Agent / Workflow Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| Agent latency | Wall time for MetricsAgent, PriceAgent, RiskAgent, AdvisorAgent spans | OpenTelemetry traces | request/run | Agent / Workflow Breakdown, Jaeger | `execution_observations.parquet`, PostgreSQL | Shows which deterministic or inference-adjacent stage dominates. |
| Tool latency | Per-tool span time, including `PriceAgent.get_history` | traces | request/run | Jaeger / Agent dashboard | `execution_observations` | Helps locate bottlenecks inside agent stages. |
| Fan-out work | Cumulative wall time across concurrent metric tasks | analytics | run | Agent dashboard / report | `charts/fanout_work.json` | Can exceed wall time because nested/concurrent tasks overlap. |
| Critical-path latency | Longest path that determines user-visible workflow time | traces + analytics | request/run | report | `charts/fanout_work.json` | More relevant to response latency than total cumulative work. |
| Invocation counts | Agent/tool call counts | metrics/traces | aggregate/run | Agent dashboard | `agent_cost_attributions.parquet` | Verifies fan-out shape and one Advisor call per successful workflow. |
| Failures | Agent/tool failed spans | traces/logs | request/run | Agent dashboard / Jaeger | `execution_observations.status/error_type` | Metrics failure prevents Risk/Advisor; Risk failure prevents Advisor. |

The collector can recover request identity at trace level for child spans that
do not repeat every request tag, and it can recover `query_id` from the
benchmark request mapping.

## Inference / vLLM Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| Requests Running | Active sequences being processed by vLLM | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json`, `resource_samples.parquet` | Scheduler occupancy and active serving concurrency. |
| Requests Waiting | Queued sequences not yet active | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Persistent growth means offered load exceeds immediate capacity. |
| Prompt Token Throughput | Input-token processing rate | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Prefill-side throughput; affected by prompt length and batching. |
| Generation Token Throughput | Output-token generation rate | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Decode throughput; often lower because output is autoregressive. |
| E2E latency | vLLM request latency histogram | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Run-level serving latency, not exact per-request attribution. |
| TTFT | Time to first generated token | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Captures queueing + prefill responsiveness. |
| TPOT | Time per output token after generation starts | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Isolates decode speed better than end-to-end latency. |
| Queue latency | Time waiting inside vLLM scheduler | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Detects model-serving overload independent of app code. |
| Prefill latency | Time to process prompt/context | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Sensitive to prompt size and batching. |
| Decode latency | Time spent generating output tokens | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Often dominant for longer completions. |
| KV-cache utilization | Context-state memory pressure | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | High values indicate pressure near sequence/context limits. |
| Preemptions | Interrupted/recomputed/swapped sequences | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Strong signal of overloaded or memory-constrained serving. |
| Prefix-cache hits/queries | Reuse of shared prompt prefixes | vLLM Prometheus | aggregate | vLLM Inference | `serving_telemetry.json` | Unavailable metrics are rendered N/A, not zero. |
| Prompt/completion/total tokens | Exact tokens for Advisor inference | inference client spans | request/run | Jaeger | `inference_observations.parquet`, PostgreSQL | Used for request-level inference cost attribution. |
| Retry count | Number of retries on inference calls | inference client | request | Jaeger | `inference_observations.parquet` | Canonical configuration uses retry count zero. |

Exact per-request inference observations come from spans/client records.
vLLM scheduler and histogram metrics are aggregate run-level telemetry.

## Resource / Hardware Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| CPU utilization | Host/container CPU activity | node-exporter/cAdvisor | aggregate | Resources / System Overview | `resource_samples.parquet` when exported | Indicates compute pressure outside vLLM scheduler state. |
| Memory | Host/container working-set memory | node-exporter/cAdvisor | aggregate | Resources | `resource_samples.parquet` | Helps identify memory pressure and container sizing issues. |
| GPU utilization | NVIDIA GPU compute utilization percent | DCGM exporter | aggregate | Resources / vLLM Inference | `resource_samples.parquet`, `serving_telemetry.json` | GPU-only; unavailable in local CPU rehearsal. |
| GPU framebuffer memory | GPU memory used, normalized from MiB to bytes | DCGM exporter | aggregate | Resources | `resource_samples.parquet` | Indicates VRAM pressure. |
| GPU power | Instantaneous GPU power in watts | DCGM exporter | aggregate | Resources | `resource_samples.parquet` | Useful for physical efficiency analysis. |
| GPU temperature | GPU temperature in Celsius | DCGM exporter | aggregate | Resources | `resource_samples.parquet` | Thermal pressure can explain throttling. |
| GPU energy | Total energy, normalized to joules where available | DCGM exporter | aggregate | Resources | `resource_samples.parquet` | Future basis for energy-grounded cost/efficiency attribution. |

Local WSL CPU runs may not expose host exporters or DCGM. Missing telemetry is
represented as N/A/unavailable, never as zero.

## Cost Metrics

| Metric | What it measures | Source | Scope | Live view | Historical storage | Interpretation / caveats |
| --- | --- | --- | --- | --- | --- | --- |
| Total infrastructure cost | Run duration times machine hourly rate | cost calculator | run | historical only | `metrics.json`, `report.json`, PostgreSQL | Connects workload duration to infrastructure economics. |
| Mean/median/p95 cost/query | Request cost distribution summary | cost calculator | run | historical only | `metrics.json`, report | Expensive tail requests can be hidden by the average. |
| Per-request cost | Attributed cost for one request | cost calculator | request | historical only | `request_cost_attributions.parquet` | Failed requests retain allocated cost. |
| Cost distribution | Histogram/CDF/box plot of request costs | chart exporter | run | historical Grafana/report | `charts/cost_query_*` | Enables workload-efficiency comparison. |
| Agent cost share | Percentage of run cost by agent plus overhead | cost calculator | run | historical Grafana/report | `agent_cost_attributions.parquet` | Prioritizes optimization work by economic impact. |
| Queries/dollar | Attempted requests divided by run cost | derived metric | run | historical Grafana/report | `metrics.json` | Workload-level efficiency within one cost profile. |
| Tokens/dollar | Inference tokens divided by run cost | derived metric | run | historical Grafana/report | `metrics.json` | Inference-efficiency signal, only comparable within a clear hardware/cost context. |

## Correlation / Debugging Dimensions

| Dimension | Meaning | Where to use it |
| --- | --- | --- |
| `run_id` | Groups one experiment run | Jaeger tags, Loki fields, raw artifacts, PostgreSQL, Parquet |
| `request_id` | Identifies one execution | Jaeger request trace, logs, request/inference rows |
| `query_id` | Links execution to benchmark input | raw request observations, analytics, trace/log fields |

These IDs are intentionally not Prometheus labels. High-cardinality labels make
time-series storage expensive and hard to query; request-level drilldown belongs
in traces, logs, and historical tables.

## Live Vs Historical

| Mode | Systems | Purpose |
| --- | --- | --- |
| Live | Grafana, Prometheus, Jaeger, Loki | Immediate operation, saturation, request debugging, and active-demo visibility |
| Historical | Parquet, PostgreSQL, `metrics.json`, `report.json`, static HTML report | Frozen, reproducible, comparable experiment records |

Both are needed: live telemetry tells you what the system is doing now;
historical artifacts preserve what happened in a measured run after live
retention windows expire.
