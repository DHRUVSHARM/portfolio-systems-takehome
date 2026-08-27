# Phase 5: Reproducible Benchmark and Load Generator

## Goal

Turn the supplied `queries.json` corpus into a reproducible experiment driver that always exercises the public Gateway rather than bypassing system layers.

```text
queries.json
  -> deterministic benchmark adapter
  -> selected workload manifest
  -> async load generator
  -> Gateway POST /v1/analyze
  -> raw per-request observations
```

## Prerequisites

- Phases 1-4 complete.
- Public Gateway endpoint stable.
- Existing deterministic `benchmark_adapter` remains authoritative for converting supplied metadata into `{holdings, lookback_days}`.
- Read `docs/architecture/system_contracts.md`.

## Dataset modes

Support at least:

- `canonical_100`: fixed committed 100-query assignment subset
- `full_1000`: all supplied records
- `sampled_N`: configurable N and seed
- optional filtered/stratified subsets for workload-shape experiments

The system must retain the full 1000-query corpus as a first-class benchmark, not reduce the project to only 100 records.

## Canonical 100 manifest

Generate once and commit a stable query-ID manifest. It must contain exactly 100 unique IDs and use deterministic stratification primarily across:

- `n_holdings`
- phrasing (`percent`, `equal`, `unweighted`)
- lookback variation where practical

Future canonical runs read the committed manifest rather than resampling.

Validate all selected records normalize with the existing adapter.

## Load model

Primary benchmark mode is closed-loop fixed concurrency.

Configurable values should include:

- dataset mode
- client concurrency C
- request timeout
- sample size/seed when applicable
- run name/id
- optional open-loop arrival-rate settings only if implemented cleanly

Do not derive Gateway admission settings, workflow workers or vLLM max_num_seqs from C.

## Load generator implementation

Use `asyncio` and one reused `httpx.AsyncClient`. Do not use one thread per request. Never allow more than the configured client concurrency outstanding.

Example: 100 total queries at C=100 means 100 total requests and at most 100 outstanding, not 10,000 requests.

For each record:

```text
select record
-> normalize deterministically
-> generate unique request_id
-> attach run_id/query_id
-> POST structured request to Gateway
-> record exact client result
```

Always benchmark through Gateway for end-to-end runs. Direct Portfolio or vLLM microbenchmarks, if added later, are separate experiment types and must never be mixed with Gateway E2E numbers.

## Correlation

Send:

- `X-Run-ID`
- `X-Request-ID`
- `X-Query-ID`

Request IDs must be unique per issued request. Query ID is the supplied dataset ID.

## Raw client observation

Persist one raw observation per attempted request including at least:

- run_id/request_id/query_id
- n_holdings
- phrasing
- normalized lookback_days
- start/finish timestamps
- client latency
- HTTP status
- success boolean
- error_type
- optional scheduled/arrival timestamp for open-loop modes

Distinguish at least:

- 503 saturation
- 429 rate limit
- other 4xx
- 5xx
- timeout
- connection failure
- malformed response

Do not silently retry failed requests. Do not throw failed rows away.

## Initial result artifact

Phase 5 may write a simple durable run directory such as:

```text
results/<run-id>/
  run.json
  resolved_benchmark_config.yaml
  requests.jsonl
```

Phase 7 will standardize PostgreSQL/Parquet and analytics. Avoid prematurely duplicating the full analytics layer here.

`run.json` should at least identify dataset mode, selected query count/manifest, concurrency, run ID, start/end time and success/failure counts.

## Optional open-loop support

Closed-loop is required first. If open-loop can be added without destabilizing the phase, keep arrival rate independent from max outstanding safety. Otherwise leave a clean interface/config placeholder in documentation, not unfinished runtime code.

## Focused tests

Required coverage:

1. all 1000 supplied records normalize successfully
2. canonical manifest has exactly 100 unique IDs
3. canonical selection is stable/reproducible
4. canonical set represents holdings-count/phrasing strata reasonably
5. full_1000 selects exactly all supplied IDs
6. sampled_N with fixed seed is deterministic
7. outstanding HTTP requests never exceed configured C
8. unique request IDs generated
9. run/query/request IDs propagated to Gateway
10. raw success latency/status recorded
11. 503/429/5xx/timeout/connection failures distinguished
12. failed requests are not retried
13. failed requests remain in results
14. E2E runner targets Gateway, not internal Portfolio
15. result manifest/run metadata is self-consistent

Use mocked or ASGI Gateway where practical; no real vLLM required.

## Acceptance criteria

Phase 5 is complete when canonical_100 is permanently reproducible, full_1000 remains available, async concurrency is exact/bounded, all requests carry correlation identity, every attempt produces a raw observation, and failures remain visible.

## Out of scope

- Prometheus/Grafana/tracing/log shipping
- exact historical Postgres/Parquet analytics
- cost accounting
- final GPU experiments