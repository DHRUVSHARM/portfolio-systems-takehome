# CPU Observability Demo Walkthrough

## Stage A: Start

Prerequisites: Docker Compose, a prepared `.env` in `deploy/compose/`, and the
CPU vLLM image used by the rehearsal path. Start the stack:

```bash
deploy/experiments/phase9_observability_demo.sh up
deploy/experiments/phase9_observability_demo.sh health
```

Expected services include Gateway, Portfolio API, vLLM, Prometheus, Grafana,
Jaeger, Loki, Alloy, OTel Collector, Alertmanager, and PostgreSQL.

## Stage B: Inspect Live Tools

```bash
deploy/experiments/phase9_observability_demo.sh urls
```

Open Grafana, Jaeger, Prometheus, and Grafana Explore for Loki.

## Stage C: One Request

```bash
deploy/experiments/phase9_observability_demo.sh single_request
```

Use the `X-Request-ID`/`request_id` value to find the trace in Jaeger and logs
in Loki. Inspect Gateway, Portfolio, MetricsAgent, PriceAgent, RiskAgent,
AdvisorAgent, and `inference.request` spans.

## Stage D: Small Benchmark

```bash
deploy/experiments/phase9_observability_demo.sh small_benchmark
```

Watch Grafana dashboards while it runs:

1. Live Benchmark
2. vLLM Inference
3. Agent / Workflow Breakdown
4. Gateway
5. System Overview
6. Resources

## Stage E: Collect

The small benchmark command exports Jaeger/Prometheus telemetry and runs the
collector. To collect an existing run:

```bash
deploy/experiments/phase9_observability_demo.sh collect_run <RUN_ID>
```

## Stage F: Historical

Open Grafana -> `Portfolio Historical Analytics` and select `run_id`,
`profile_id`, and `request_id`. Open the static report:

```text
results/phase8/analytics/<RUN_ID>/report/index.html
```

## Stage G: Raw Artifacts

Use:

```bash
deploy/experiments/phase9_observability_demo.sh show_paths <RUN_ID>
```

The Parquet, JSON, and SVG files are the auditable source behind the report.
