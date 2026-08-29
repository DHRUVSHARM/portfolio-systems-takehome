# Architecture Decisions

## vLLM and One Shared Inference Service

vLLM provides an OpenAI-compatible serving interface for the selected
open-weight Qwen model. A single shared inference service keeps application code
device-agnostic: CPU rehearsal and canonical GPU deployment expose the same
`/v1/chat/completions` contract.

## One Advisor Inference Call

The benchmark measures the supplied multi-agent workflow with exactly one
Advisor inference call per successful request. Query normalization remains
deterministic and does not add an LLM parsing step.

## Prometheus, Jaeger, Loki

Prometheus owns live aggregate metrics. OpenTelemetry and Jaeger own exact
request traces. Loki owns JSON logs. Request IDs are intentionally excluded from
Prometheus labels to avoid high-cardinality metrics.

## PostgreSQL and Parquet

PostgreSQL is the queryable historical source for dashboards and drilldown.
Parquet keeps each run portable for offline analysis and review. Both are
derived from the same raw observations.

## Raw Telemetry Retention

Normalized fields make common analysis easy, while raw fields preserve source
evidence for later recalculation and debugging.

## Versioned Cost Profiles

Cost profiles are external and versioned so historical observations can be
recalculated without rerunning inference. CPU rehearsal cost is explicitly
illustrative; canonical GPU economics require a real provider machine rate.

## Aggregate vs Per-Request Serving Data

vLLM Prometheus histograms and scheduler gauges are aggregate run-level signals.
They are not attached to individual requests unless the exact per-request value
exists in an inference observation.

## Reproducibility

Run provenance includes git SHA, config hashes, model-serving configuration,
cost profile, and hardware profile where available. Historical analytics should
not depend on temporary Jaeger retention.
