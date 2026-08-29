# Phase 4: Public Gateway and Admission Control

## Goal

Create the public FastAPI Gateway in front of the internal Portfolio API. The Gateway is the public/control-plane boundary for validation, request identity, bounded admission, bounded waiting, timeout/cancellation handling and downstream connection reuse.

```text
clients
  -> POST /v1/analyze
  -> Gateway
      validation
      correlation IDs
      admission controller
      bounded queue
      timeout/cancellation
  -> Portfolio API /internal/analyze
```

## Prerequisites

- Phases 1-3 complete.
- Read `docs/architecture/system_contracts.md`.
- Treat the Portfolio API contract as fixed; do not move workflow logic into Gateway.

## Suggested package

```text
services/gateway/
  __init__.py
  app.py
  models.py
  config.py
  admission.py
  client.py
```

## Public endpoint

Required:

```text
POST /v1/analyze
GET /health
GET /ready
```

The public request is the structured workflow request: holdings + lookback_days. The `queries.json` natural-language/metadata adapter remains benchmark-layer plumbing and must not be moved into Gateway.

## Independent configuration

Externalize at least:

- Portfolio base URL
- `max_in_flight` (G)
- `queue_capacity` (Q)
- `queue_timeout_seconds`
- `downstream_timeout_seconds`
- `rate_limit_enabled`, default false for canonical benchmark

Do not conflate these with client concurrency C, workflow CPU workers W, metric-task limit M or vLLM max_num_seqs V.

## AdmissionController semantics

A plain semaphore is insufficient because it permits unlimited coroutine waiters. Explicitly bound both:

```text
active downstream requests <= max_in_flight
waiting requests <= queue_capacity
```

Logical states should be observable internally:

```text
received -> validated -> immediately admitted
                     or -> queued -> admitted
                     or -> rejected
                     or -> queue timed out/cancelled
```

When active capacity is free, admit immediately. When active is full but queue has room, wait in the bounded queue. When both are full, reject immediately with HTTP 503.

HTTP 429 is reserved for deliberate caller rate limiting. Canonical benchmark rate limiting is disabled, so capacity saturation maps to 503.

## Cancellation correctness

If a client disconnects/cancels while queued, remove/cancel its pending admission so it cannot later execute against Portfolio. Active capacity must always be released in `finally` paths. Cancellation of an active downstream call should propagate where safe.

## Timeout model

Measure and enforce queue timeout separately from downstream execution timeout. Do not use one ambiguous whole-request timeout if it prevents attribution of waiting vs service time.

## Downstream client

Use one process-lifetime reused `httpx.AsyncClient` to call Portfolio `/internal/analyze`. Construct/close it through Gateway lifespan. Do not create one client per request and do not retry whole workflow requests automatically.

## Correlation

For every accepted public request establish:

- optional run_id
- required/generated request_id
- optional query_id

Accept/return standard project headers and forward them unchanged to Portfolio. Generated IDs must be unique enough for benchmark correlation.

## Failure mapping

Keep these distinguishable in response/status/internal observation:

- validation error
- 503 admission rejection
- queue timeout
- downstream timeout
- downstream non-2xx/5xx
- connection failure
- cancellation

Do not hide downstream failures behind a generic successful response.

## Connection concurrency

Use async FastAPI/httpx. One Uvicorn worker is the initial design so admission limits are truly process-global and do not silently multiply per worker. A single async process can hold many sockets. Do not introduce one thread per request.

## Focused tests

Use a mocked/fake Portfolio downstream.

Required coverage:

1. active downstream calls never exceed `max_in_flight`
2. number waiting never exceeds `queue_capacity`
3. request beyond active + queue gets immediate 503
4. queued request runs after active capacity frees
5. queued cancellation never later executes downstream
6. queue timeout releases waiting capacity correctly
7. downstream error/timeout releases active capacity
8. request IDs generated when absent
9. supplied run/request/query IDs forwarded unchanged
10. one shared downstream AsyncClient lifecycle
11. no automatic whole-workflow retry
12. high-connection gateway-only stress-style test with cheap fake downstream demonstrates bounded active/queue behavior without thread explosion

## Checkpoint 1

After focused tests, run the full existing Python test suite once. This is the first explicit checkpoint because the complete request path now exists:

```text
Client -> Gateway -> Portfolio API -> PortfolioRuntime -> agents -> vLLM interface
```

Perform Git review at this checkpoint before relying on the path for benchmark instrumentation.

## Acceptance criteria

Phase 4 is complete when Gateway provides bounded active work and bounded waiting, controlled 503 overload, correct cancellation/timeouts, stable request identity, process-lifetime downstream connections and a tested end-to-end application request boundary.

## Out of scope

- benchmark dataset/load generator
- Prometheus/OpenTelemetry/log shipping
- PostgreSQL/Parquet
- cost calculations
- Docker/GPU deployment