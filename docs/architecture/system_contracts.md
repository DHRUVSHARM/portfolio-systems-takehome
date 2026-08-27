# System Implementation Contracts

This document is authoritative for Phases 2-8. Later phases may add implementation detail, but must not silently change these contracts.

## Workload under test

The supplied portfolio workflow remains the workload. Preserve the semantic order:

```text
holdings
  -> per-ticker MetricsAgent fan-out
       -> MetricsAgent calls PriceAgent
  -> barrier: all ticker metrics complete successfully
  -> RiskAgent
  -> AdvisorAgent
  -> one LLM inference call
```

Do not redesign agent responsibilities, formulas, query semantics, or response shape unless fixing a demonstrated bug. During canonical benchmarking use deterministic/synthetic PriceAgent mode so external finance-network variance does not contaminate inference measurements.

## Request identity

Use the existing immutable `RequestContext` as the correlation contract:

- `run_id: str | None`
- `request_id: str | None`
- `query_id: str | None`

Propagate these identifiers through benchmark -> Gateway -> Portfolio API -> PortfolioRuntime -> Advisor -> inference observations. IDs belong in traces/log fields and exact historical observations. Do not use run/request/query IDs as Prometheus labels.

## API contracts

Public API, implemented in Phase 4:

```text
POST /v1/analyze
```

Internal Portfolio API, implemented in Phase 3:

```text
POST /internal/analyze
GET /health
GET /ready
```

Logical structured request:

```json
{
  "holdings": {"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
  "lookback_days": 180
}
```

Correlation headers:

- `X-Run-ID`
- `X-Request-ID`
- `X-Query-ID`

The business response remains the existing workflow result containing holdings, lookback_days, metrics, risk and summary. Internal telemetry must not be added to this public business result.

## Canonical inference contract

Canonical model:

`Qwen/Qwen3-4B-Instruct-2507`

Serving interface:

```text
POST /v1/chat/completions
OpenAI-compatible HTTP
```

Canonical generation defaults:

- temperature: `0.0`
- max_tokens: `256`
- retry_count: `0`
- stream: `false`
- exactly one Advisor inference call per successful workflow request

CPU and GPU deployments expose the same vLLM API. Application code must not branch on device type.

Do not perform LLM-based normalization of `queries.json`. The supplied metadata is normalized deterministically through the existing benchmark adapter so the benchmark preserves one LLM call per portfolio query.

## Concurrency contracts

Keep these independent:

- `C`: benchmark/client concurrency
- `G`: Gateway `max_in_flight`
- `Q`: Gateway waiting queue capacity
- `W`: Portfolio workflow CPU-worker capacity
- `M`: global concurrent metric-task limit
- `V`: vLLM `max_num_seqs`

Do not rename one concept into another or derive them implicitly from each other.

Phase 1 already owns process-wide bounded CPU execution. Later phases must not create a ThreadPoolExecutor per request or wrap the whole workflow in a worker thread.

Gateway admission must bound both active downstream work and waiting work. A semaphore with unlimited waiting coroutines is not sufficient.

## Failure contracts

Canonical benchmark failures must remain visible.

- Metrics failure prevents Risk and Advisor.
- Risk failure prevents Advisor.
- Canonical inference failure propagates; no silent deterministic/Bedrock fallback.
- Canonical inference retry count is zero.
- Queued Gateway cancellation must not execute later.
- Observability backend failure must not crash serving.
- Service/container restart during a canonical measured run invalidates or fails that run unless it is explicitly a resilience experiment.

## Benchmark corpus

The full supplied `queries.json` contains 1000 records and remains first-class.

Required modes:

- `canonical_100`: fixed, committed, reproducible assignment subset
- `full_1000`: all records
- `sampled_N`: configurable N + seed
- optional filtered/stratified subsets for workload-shape experiments

Primary benchmark traffic model is async closed-loop fixed concurrency using `asyncio` + one reused `httpx.AsyncClient`.

## Observability ownership

Use each system for the data it is suited to:

- Prometheus: low-cardinality live aggregate operational metrics
- OpenTelemetry + Jaeger: per-request trace hierarchy and critical-path debugging
- JSON stdout + Grafana Alloy + Loki: correlated logs
- PostgreSQL: authoritative structured historical experiment observations and derived analytics
- Parquet: immutable/portable run artifacts
- native vLLM `/metrics`: scheduler, KV-cache and inference-engine telemetry
- node-exporter/cAdvisor: host/container resources
- DCGM exporter on GPU hosts: physical NVIDIA GPU telemetry

Prometheus labels must never include request_id, query_id, run_id, trace_id, arbitrary user IDs, or per-request identifiers. Per-ticker exact detail belongs in traces and PostgreSQL/Parquet rather than Prometheus labels.

## Exact execution hierarchy

Historical and trace representations should be able to express:

```text
run
└ request
  └ portfolio
    ├ metrics stage
    │ ├ MetricsAgent[AAPL]
    │ │ └ PriceAgent.get_history[AAPL]
    │ ├ MetricsAgent[MSFT]
    │ │ └ PriceAgent.get_history[MSFT]
    │ └ ...
    ├ RiskAgent.assess
    └ AdvisorAgent.summarize
      └ inference.request
```

Capture both wall time and CPU time where meaningful. Concurrent fan-out cumulative work is not the same as critical-path stage latency.

## Cost contracts

Do not compute costs in serving agents.

For a rented bundled VM, authoritative experiment infrastructure cost is:

```text
measured run duration hours * actual machine hourly rate
```

Do not double-count GPU/CPU/RAM if they are already included in the provider's machine price.

Attributed costs must be derived downstream and satisfy:

```text
sum(request attributed costs) ~= attributable run cost
```

CPU attribution should use CPU-seconds where available. GPU inference attribution must not use `request latency * GPU hourly price` for concurrent requests because continuous batching would double-count overlapping GPU time. Baseline GPU allocation should use a configurable shared cost pool and token-work weights; future energy-based attribution may be added without changing raw observations.

## Deployment contract

Docker Compose is the executable deployment mechanism. Kubernetes is out of the required runtime path.

Use a common stack plus CPU/GPU overrides. The same Gateway, Portfolio and API contracts must work locally and on the cloud GPU host. vLLM, PostgreSQL and telemetry backends stay on private Compose networks unless there is a deliberate reason to expose them.

Canonical final model serving is single-GPU BF16. Do not add tensor parallelism, speculative decoding, LoRA, RAG, manual batching or multi-model complexity unless a concrete later requirement appears.

## Scope discipline

Each phase document is authoritative for that phase. Codex must inspect existing code before editing, preserve completed phases, implement only the current phase, run the focused tests requested by that phase, and avoid opportunistic future-phase work.