# Verified 100-Query CPU Baseline

Run ID: `NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z`

- Dataset: `canonical_100`
- Concurrency: `2`
- Model: `Qwen/Qwen3-0.6B`
- Result: `100/100 successful`
- Environment: noncanonical local CPU rehearsal

## Start Here

- Generated report: `analytics/report/index.html`
- Derived metrics: `analytics/metrics.json`
- Compact run summary: `analytics/report.json`
- Actual API responses: `raw/requests.jsonl`
- Resolved benchmark configuration: `raw/resolved_benchmark_config.yaml`
- Frozen Jaeger traces: `telemetry/`
- Frozen Prometheus samples: `telemetry/`

The analytics directory also contains Parquet tables for request, execution,
inference, resource, and cost-attribution analysis.

Dollar values use the illustrative CPU cost profile and are not production GPU
cost claims.
