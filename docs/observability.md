# Observability

## Live Locations

- Grafana: `http://localhost:3000`
- Jaeger: `http://localhost:16686`
- Prometheus: `http://localhost:9090`
- Loki: open Grafana -> Explore -> Loki datasource
- Gateway: `http://localhost:8000`

## Correlation

Use `run_id`, `request_id`, and `query_id` in traces, logs, benchmark artifacts,
PostgreSQL, and Parquet. Do not search for those IDs as Prometheus labels.

For one slow request:

1. Find the request in the benchmark output or Gateway response headers.
2. Search Jaeger for spans tagged with the request/run/query identifiers.
3. Inspect Gateway admission, Portfolio workflow, Metrics/Price fan-out,
   RiskAgent, AdvisorAgent, and `inference.request`.
4. In Grafana Explore, query Loki for the same JSON fields.
5. After collection, open Historical Analytics or `report/index.html` for the
   frozen run view.

## Grafana to Logs/Traces

The Loki datasource includes a derived field for JSON `trace_id` values that
links to the Jaeger datasource. If a direct link is unavailable in a local
Grafana version, use the trace ID manually in Jaeger search.
