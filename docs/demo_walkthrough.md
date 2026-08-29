# CPU Observability Demo Walkthrough

This walkthrough is for the noncanonical CPU rehearsal. It uses
`Qwen/Qwen3-0.6B`, synthetic/reference CPU cost, and local Docker Compose. The
canonical GPU benchmark path remains unchanged and should be run later on the
GPU host.

## Start The Stack

Prerequisites: Docker Compose, the project checkout, and `deploy/compose/.env`
prepared for the CPU rehearsal. Start and check health:

```bash
deploy/experiments/phase9_observability_demo.sh up
deploy/experiments/phase9_observability_demo.sh health
deploy/experiments/phase9_observability_demo.sh urls
```

The CPU/WSL path is expected to be slower and more memory-constrained than the
GPU path. DCGM GPU telemetry is not required in CPU mode, so GPU panels may show
`N/A / telemetry unavailable for this run`.

## Single Request Drilldown

Run one known request:

```bash
deploy/experiments/phase9_observability_demo.sh single_request
```

The CPU script sends these fixed identity values:

| Field | Value |
| --- | --- |
| run ID | `NONCANONICAL_CPU_INTEGRATION_SINGLE` |
| request ID | `cpu-rehearsal-single` |
| query ID | `cpu-rehearsal-single` |

In Jaeger, open `$JAEGER_BASE_URL`, select service `gateway`, and search tags:

```json
{"run_id":"NONCANONICAL_CPU_INTEGRATION_SINGLE","request_id":"cpu-rehearsal-single"}
```

The trace should show Gateway, Portfolio API/runtime, MetricsAgent/PriceAgent,
RiskAgent, AdvisorAgent, and `inference.request` spans.

In Grafana Explore, choose the Loki datasource and run:

```logql
{service_name=~"gateway|portfolio-api"} | json | run_id="NONCANONICAL_CPU_INTEGRATION_SINGLE" | request_id="cpu-rehearsal-single"
```

The parsed JSON fields should include `run_id`, `request_id`, `query_id`, span
or trace IDs where available, service name, level, and message.

## Small Benchmark

Run the noncanonical sampled benchmark:

```bash
deploy/experiments/phase9_observability_demo.sh small_benchmark
```

The command prints the resulting `RUN_ID` and next commands. During the run,
watch:

| Dashboard | What To Check |
| --- | --- |
| Live Benchmark | request rate, latency, success/failure shape during load |
| Gateway | bounded in-flight work, queue depth, queue timeout/rejection behavior |
| Agent / Workflow Breakdown | agent/tool call rates, p95 latency, CPU slot pressure |
| vLLM Inference | scheduler queue/running requests, tokens, KV cache, latency histograms |
| System Overview | service health and host/container load |
| Resources | CPU/RAM/container telemetry; GPU rows only on GPU hosts |

## 100-Query CPU Baseline

Run the verified baseline shape:

```bash
deploy/experiments/phase9_observability_demo.sh run_100 2
```

For deliberate local concurrency exploration, the wrapper also accepts:

```bash
deploy/experiments/phase9_observability_demo.sh run_100 16
```

The submission evidence uses the successful C2 artifact documented in
[experiment_results.md](experiment_results.md). Do not use the failed CPU
full_1000/C100 stress attempt as final evidence.

## Collect Or Re-render

To collect an existing run:

```bash
deploy/experiments/phase9_observability_demo.sh collect_run <RUN_ID>
```

To regenerate the static report:

```bash
deploy/experiments/phase9_observability_demo.sh render_report <RUN_ID>
```

To verify expected artifacts:

```bash
deploy/experiments/phase9_observability_demo.sh verify_run <RUN_ID>
deploy/experiments/phase9_observability_demo.sh show_paths <RUN_ID>
```

## Historical Inspection

Open Grafana -> `Portfolio Historical Analytics`, then select `run_id`,
`profile_id`, and `request_id`.

The dashboard exposes the KPI row, experiment comparison, agent cost share,
cost/query distribution, selected-request summary, execution observations,
inference observations, and run-level serving telemetry.

The generated static report is at:

```text
results/phase8/analytics/<RUN_ID>/report/index.html
```

Open it from the checkout or artifact bundle. Its artifact index uses relative
links, so the report remains portable when copied with the run directory.

## Artifact Map

| Artifact | Purpose |
| --- | --- |
| `results/phase8/raw/<RUN_ID>/run.json` | benchmark run metadata and request observations |
| `results/phase8/raw/<RUN_ID>/observations.jsonl` | raw per-request benchmark observations |
| `results/phase8/telemetry/<RUN_ID>/*_jaeger_traces.json` | frozen trace export |
| `results/phase8/telemetry/<RUN_ID>/*_prometheus_samples.json` | frozen Prometheus range export |
| `results/phase8/analytics/<RUN_ID>/requests.parquet` | canonical request observations |
| `results/phase8/analytics/<RUN_ID>/execution_observations.parquet` | agent/tool/span observations |
| `results/phase8/analytics/<RUN_ID>/inference_observations.parquet` | exact Advisor inference observations |
| `results/phase8/analytics/<RUN_ID>/resource_samples.parquet` | host/container/vLLM/GPU telemetry samples |
| `results/phase8/analytics/<RUN_ID>/request_cost_attributions.parquet` | exact cost per request |
| `results/phase8/analytics/<RUN_ID>/agent_cost_attributions.parquet` | agent cost share plus `overhead_unallocated` |
| `results/phase8/analytics/<RUN_ID>/metrics.json` | recalculated assignment metrics |
| `results/phase8/analytics/<RUN_ID>/report.json` | compact run summary |
| `results/phase8/analytics/<RUN_ID>/serving_telemetry.json` | aggregate vLLM/GPU serving summary |
| `results/phase8/analytics/<RUN_ID>/report/index.html` | reviewer-facing portable report |
