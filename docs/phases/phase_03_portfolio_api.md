# Phase 3: Internal Portfolio API

## Goal

Expose the completed PortfolioRuntime and async Advisor path behind a dedicated internal FastAPI service. This service is the application/workflow boundary used by the public Gateway later.

```text
POST /internal/analyze
  -> PortfolioRuntime
  -> Metrics fan-out/barrier
  -> Risk
  -> async Advisor/vLLM client
  -> existing portfolio response
```

## Prerequisites

- Phase 1 runtime complete.
- Phase 2 async inference path complete.
- Read `docs/architecture/system_contracts.md`.
- Inspect existing runtime/agent lifecycle before adding service-level ownership.

## Service package

Prefer a small dedicated package such as:

```text
services/portfolio_api/
  __init__.py
  app.py
  models.py
  config.py
```

Do not move working business logic into the service package merely for aesthetics.

## Endpoints

Required:

```text
POST /internal/analyze
GET /health
GET /ready
```

`POST /internal/analyze` accepts structured workflow input only:

- holdings: mapping ticker -> weight
- lookback_days: positive integer, default 365

Benchmark natural-language metadata normalization does not belong here.

## Request validation

Validate at the API boundary:

- holdings non-empty
- ticker keys usable/non-empty strings
- weights numeric and positive
- lookback_days positive

Do not silently normalize arbitrary malformed portfolios here. Existing benchmark normalization remains upstream in Phase 5.

## Correlation

Accept headers:

- `X-Run-ID`
- `X-Request-ID`
- `X-Query-ID`

Construct `RequestContext`. If request ID is absent, generate one. Preserve supplied IDs exactly when valid. Return at least request ID in response headers; returning run/query IDs as headers is fine if consistent.

Do not add telemetry identifiers to the business response body.

## Lifespan ownership

Use FastAPI lifespan/startup-shutdown to construct process-lifetime objects once:

```text
PriceAgent
MetricsAgent
RiskAgent
InferenceClient
VLLMAdvisorAgent
PortfolioRuntime
```

Reuse them across all HTTP requests. On shutdown, close runtime/inference resources cleanly. Do not create ThreadPoolExecutor or AsyncClient per request.

The service route must be async and call `await runtime.analyze(...)`. Do not wrap the whole workflow in `to_thread`.

## Price mode

Support external configuration for deterministic benchmark mode. Canonical benchmark mode uses:

```python
PriceAgent(use_yfinance=False)
```

Real yfinance mode may remain available for demos/noncanonical use. Do not change PriceAgent finance calculations.

## Health and readiness

`GET /health`: process is alive.

`GET /ready`: runtime/service initialization completed and requests may be accepted.

Readiness must not issue an expensive model generation. It also must not depend on Prometheus, Jaeger, Loki or other optional observability backends.

If a cheap inference-server health probe is introduced later, keep readiness semantics explicit and timeout-bounded.

## Failure behavior

Workflow/inference failures return controlled non-success HTTP responses. Do not expose raw stack traces, prompts, secrets or credentials. Do not silently invoke legacy Advisor fallback on canonical service traffic.

Preserve useful distinctions between validation failure and internal/downstream execution failure where practical.

## Configuration

Externalize at least:

- deterministic/yfinance price mode
- runtime CPU worker count
- max concurrent metric tasks
- inference configuration references/values needed to construct Phase 2 client

Do not add Gateway admission parameters here.

## Focused tests

Use FastAPI/ASGI testing with injected fake runtime or mocked inference as appropriate. No real vLLM.

Required coverage:

1. valid request invokes runtime exactly once
2. structured response shape preserved
3. header IDs form correct RequestContext
4. missing request ID generates one
5. provided request/run/query IDs are preserved
6. invalid holdings rejected
7. invalid lookback rejected
8. runtime/workflow failure produces controlled non-2xx response
9. `/health` works independently of observability
10. `/ready` reflects initialization state
11. process lifespan creates one runtime and reuses it across requests
12. shutdown closes owned resources
13. benchmark deterministic PriceAgent mode can be selected via configuration

Run focused Phase 3 tests only.

## Acceptance criteria

Phase 3 is complete when one long-lived PortfolioRuntime can be exercised through `/internal/analyze`, correlation propagates correctly, lifecycle is process-scoped, deterministic benchmark price mode is configurable, and workflow failures remain visible.

## Out of scope

- public Gateway
- admission queue/rate limiting
- benchmark/load generator
- Prometheus/tracing/log shipping
- Docker Compose
- PostgreSQL/Parquet
- cost accounting