# Phase 9: Observability, Analytics, Visualization, and Demo

Phase 9 turns the completed serving, benchmark, telemetry, and cost pipeline
into a reviewer-facing experience.

## Scope

Implemented in this phase:

- Static visual report generation at `report/index.html` for collected runs.
- SVG charts rendered from existing persisted `charts/*.json` datasets.
- Run-level serving telemetry interpretation from exported Prometheus samples.
- Request -> execution observation -> inference drilldown in the report.
- Historical Grafana dashboard variables and drilldown panels.
- Loki trace-id derived field configuration for Jaeger navigation.
- CPU demo wrapper commands in `deploy/experiments/phase9_observability_demo.sh`.
- Documentation for architecture, observability, analytics, cost, benchmark,
  demo, and troubleshooting paths.

Not implemented in this phase:

- Real GPU experiments.
- Final GPU results narrative.
- New workflow semantics or application behavior.

## Generated Report Outputs

Collected analytics directories now include:

```text
report/index.html
report/assets/*.svg
report/assets/chart_manifest.json
serving_telemetry.json
```

The report is static and portable. It reads only persisted artifacts from the
run directory and does not require Grafana, PostgreSQL, Jaeger, Prometheus, or a
Python server to view.

## Acceptance Checklist

- Assignment metrics are visible at the top of the HTML report.
- Synthetic CPU cost profiles are labeled `NONCANONICAL / ILLUSTRATIVE COST ONLY`.
- Missing optional serving/GPU telemetry is shown as `N/A / telemetry unavailable for this run`.
- Aggregate vLLM Prometheus data is clearly labeled run-level telemetry.
- Per-request inference fields come only from exact inference observations.
- Historical Grafana supports run/profile/request drilldown.
