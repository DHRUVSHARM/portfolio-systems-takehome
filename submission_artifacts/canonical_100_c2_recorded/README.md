# Recorded 100-Query C2 CPU Run

Run ID: `NONCANONICAL_CPU_CANONICAL_100_C2-20260829T020534Z`

- Dataset: `canonical_100`
- Concurrency: `2`
- Request timeout: `300s`
- Model: `Qwen/Qwen3-0.6B`
- Result: `100/100 successful`
- Environment: noncanonical local CPU rehearsal

## Purpose

This is the successful 100-query C2 run shown in the two silent submission videos:

- Video 1 captures the beginning of the experiment and live observability.
- Video 2 captures the completed run and post-run analytics/report.

## Start Here

- Generated report: `analytics/report/index.html`
- Derived metrics: `analytics/metrics.json`
- Run summary: `analytics/report.json`
- Actual API responses: `raw/requests.jsonl`
- Resolved benchmark configuration: `raw/resolved_benchmark_config.yaml`
- Frozen Jaeger traces: `telemetry/`
- Frozen Prometheus samples: `telemetry/`

The analytics directory also contains Parquet datasets for request, execution,
inference, resource, and cost-attribution analysis.

Dollar values use the illustrative CPU cost profile and are not production GPU
cost claims.
