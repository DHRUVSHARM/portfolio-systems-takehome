# 10-Query Integration Rehearsal

Run ID: `NONCANONICAL_CPU_INTEGRATION-20260828T205107Z`

- Dataset: `sampled_10`
- Sample seed: `81`
- Concurrency: `2`
- Model: `Qwen/Qwen3-0.6B`
- Result: `10/10 successful`
- Environment: noncanonical local CPU rehearsal

## Start Here

- Generated report: `analytics/report/index.html`
- Derived metrics: `analytics/metrics.json`
- Compact run summary: `analytics/report.json`
- Actual API responses: `raw/requests.jsonl`
- Frozen Jaeger traces: `telemetry/`
- Frozen Prometheus samples: `telemetry/`

This run validates the complete benchmark -> telemetry -> analytics -> report
pipeline before the 100-query baseline.

Dollar values use the illustrative CPU cost profile.
