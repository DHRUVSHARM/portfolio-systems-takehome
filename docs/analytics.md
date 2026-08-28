# Analytics

Historical analytics are produced by the collector from persisted observations,
not from live workflow objects.

## PostgreSQL Tables

- `experiment_runs`
- `requests`
- `execution_observations`
- `inference_observations`
- `resource_samples`
- `cost_profiles`
- `cost_analyses`
- `request_cost_attributions`
- `agent_cost_attributions`
- `derived_metrics`
- `metric_registry`

Useful views:

- `experiment_cost_comparison`
- `agent_cost_breakdown`

## Generated Files

For a run ID `<RUN_ID>`:

```text
results/phase8/analytics/<RUN_ID>/run.json
results/phase8/analytics/<RUN_ID>/provenance.json
results/phase8/analytics/<RUN_ID>/metrics.json
results/phase8/analytics/<RUN_ID>/report.json
results/phase8/analytics/<RUN_ID>/serving_telemetry.json
results/phase8/analytics/<RUN_ID>/report/index.html
results/phase8/analytics/<RUN_ID>/*.parquet
results/phase8/analytics/<RUN_ID>/charts/*.json
results/phase8/analytics/<RUN_ID>/report/assets/*.svg
```

The HTML report is portable. Historical Grafana is interactive and
database-backed.

## Drilldown

Use `Portfolio Historical Analytics` with variables:

- `run_id`
- `profile_id`
- `request_id`

The dashboard exposes request summary, execution observations, inference
observations, request cost attribution, run-level serving telemetry, and
experiment comparison.
