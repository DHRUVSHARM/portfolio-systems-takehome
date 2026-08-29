# Phase 7: Historical Analytics and Cost Accounting

## Goal

Persist exact experiment observations before deriving metrics, then implement versioned analytics and cost accounting required by the assignment. Historical runs must be recalculable under new cost profiles without rerunning inference.

## Prerequisites

- Phases 1-6 complete and Checkpoint 2 reviewed.
- Benchmark request identity and observability semantics stable.
- Read `docs/architecture/system_contracts.md`.

## Core principle

Never throw away raw measurements in favor of precomputed averages. Persist raw benchmark, request, stage/agent/tool/ticker, inference and relevant resource observations first. Derived metrics are downstream and versioned.

## Suggested analytics structure

```text
analytics/
  models.py
  registry.py
  calculators/
    cost.py
    latency.py
    throughput.py
    agents.py
    inference.py
    resources.py
    saturation.py
  exporters/
    postgres.py
    parquet.py
    report.py
```

Adapt to existing package conventions, but preserve separation between raw observations, calculators and exporters.

## PostgreSQL data model

Use PostgreSQL as the authoritative structured historical store. Implement migrations/schema in a maintainable conventional way.

### experiment_runs

Capture enough provenance to interpret every result:

- run_id
- started_at/finished_at/duration
- status/invalid reason
- git commit/config hashes where available
- dataset mode/query count/selection manifest or seed
- benchmark concurrency/open-loop settings if used
- Gateway max_in_flight/queue configuration
- workflow cpu_workers/metric-task limit
- model/model revision
- vLLM version
- dtype
- max_model_len
- max_num_seqs
- max_num_batched_tokens
- gpu_memory_utilization
- prefix-caching state
- hardware profile
- cost profile/version

Allow fields unavailable in unit/local runs to be nullable; do not invent values.

### requests

Store one row per attempted benchmark request including:

- run/request/query IDs
- n_holdings/phrasing/lookback
- client timing
- server/Gateway timing if available
- Gateway queue wait if available
- HTTP status/success/error_type

Failed requests must remain in the dataset.

### execution_observations

Historical representation of workflow hierarchy:

- observation_id
- parent_observation_id
- run/request/query IDs
- stage
- agent
- tool optional
- ticker optional
- started_at/finished_at
- wall_time_ms
- cpu_time_ms where measured
- status/error_type

This must support request -> stage -> agent -> tool -> ticker drill-down.

### inference_observations

Persist Phase 2 evidence plus later server-derived timing where available:

- inference ID
- run/request/query IDs
- agent/model
- start/end/elapsed
- prompt/completion/total tokens
- TTFT
- queue/prefill/decode/generation timing where available
- mean ITL/TPOT/tokens-per-second where available
- status/error/attempt count

Treat version-dependent vLLM fields as optional rather than fabricating them.

### resource_samples

Persist relevant sampled resource observations when exported:

- run_id/timestamp
- resource type/id
- CPU utilization
- memory bytes
- GPU utilization
- GPU memory used
- GPU power
- GPU temperature
- GPU energy where available
- network RX/TX where useful

Do not pretend sampled GPU utilization belongs exactly to one request.

## Parquet run artifact

Every completed run must be exportable to a portable directory such as:

```text
results/<run-id>/
  run.json
  resolved_config.yaml
  requests.parquet
  execution_observations.parquet
  inference_observations.parquet
  resource_samples.parquet
  metrics.json
  summary.csv
  charts/
```

PostgreSQL and Parquet represent the same underlying observations. `run.json` must make the run self-describing enough to compare later.

## Metric registry

Use a registry/calculator contract such as:

```python
class MetricCalculator:
    name: str
    version: str
    def calculate(self, dataset, context): ...
```

Configuration selects calculators/percentiles. Adding a new calculator must not require workflow or Gateway changes.

Derived metric records should retain metric name/version, calculated_at and relevant cost-profile/version.

## Cost profiles

Externalize versioned pricing and attribution under e.g. `configs/cost/`.

At minimum:

- profile name/version
- `machine_hourly_usd`
- CPU attribution method
- GPU attribution method
- configurable prefill/decode token-work weights

Do not hardcode cloud price in agents, Gateway, benchmark runner or calculators. Phase 8 fills the actual rented machine rate when known.

## Authoritative run cost

For a bundled rented VM:

```text
total infrastructure cost = measured run duration hours * actual machine hourly rate
```

Do not add full CPU + GPU + RAM costs again when the provider rate already bundles them.

Local runs may use an explicitly labeled reference/estimated profile; never present that as actual cloud billing.

## Attribution model

Attributed cost is an analytical allocation of the shared run cost, not a claim about provider billing breakdown.

### CPU

Allocate CPU-related pool/share using measured CPU-seconds where practical, not overlapping wall time. This is necessary because per-ticker fan-out runs concurrently.

### GPU

Do not compute `request inference latency * GPU hourly rate`; continuous batching makes request wall times overlap and that method double-counts GPU cost.

Baseline allocation should use a shared GPU/run pool with configurable token-work weights:

```text
work_i = prefill_weight * prompt_tokens_i + decode_weight * completion_tokens_i
request_gpu_cost_i = work_i / sum(work) * gpu_cost_pool
```

Support future energy-based allocation without changing raw stored observations.

## Accounting invariants

For an attributable run:

```text
sum(request attributed cost) ~= total attributable run cost
```

Agent cost shares should sum to approximately 100% under the selected reporting boundary. Use explicit floating-point tolerance and make unallocated/overhead buckets visible rather than silently losing cost.

## Assignment-required metrics

Implement exactly:

1. Total cost
2. Exact cost per successful/query attempt according to documented policy
3. Mean/median/p50/p75/p90/p95/p99/min/max cost per query, plus useful spread (stddev/IQR)
4. Percentage of cost per agent: PriceAgent, MetricsAgent, RiskAgent, AdvisorAgent; expose overhead/unallocated if methodology requires it
5. Cost-per-query distribution data and charts: histogram, box plot, CDF

Clearly document treatment of failed requests in cost-per-query statistics. Preserve their allocated infrastructure cost where appropriate rather than pretending failures were free.

## Additional calculators

Implement useful systems metrics from persisted observations:

- E2E/Gateway/stage/agent/tool latency percentiles
- cumulative agent work vs stage critical-path wall time
- request throughput
- prompt/completion/total token throughput
- TTFT/TPOT/ITL distributions where available
- inference queue pressure
- KV-cache utilization
- prefix-cache hit ratio
- preemption rate
- CPU/GPU/memory efficiency
- queries per dollar
- tokens per dollar
- tokens per joule if energy data exists
- saturation/concurrency knee inputs

## Agent analytics

Enable historical queries for each agent and tool:

- calls
- wall time
- CPU time
- p50/p95/p99 latency
- failures
- attributed cost
- cost percentage

Support exact ticker/request drill-down through Postgres, not Prometheus.

## Historical Grafana

Add PostgreSQL as a Grafana historical datasource and provision dashboards/views for:

### Experiment comparison

Compare selected runs across throughput, p50/p95/p99, TTFT, total cost, cost/query, tokens/sec, GPU/KV behavior and relevant configuration.

### Agent cost breakdown

For a selected run show agent, calls, CPU seconds, wall time, p95 latency, cost and cost percent. Allow filtering/drill-down by agent/tool/ticker/request through SQL-backed views/queries.

### Query/workload shape

Useful relationships include holdings count vs latency/cost, lookback vs Metrics work, prompt tokens vs Advisor latency, completion tokens vs cost.

## Reports and charts

Generate reproducible outputs strictly from persisted observations. At minimum:

- cost/query histogram
- cost/query box plot
- cost CDF
- latency distribution
- cost/latency by holdings count
- cost vs prompt/completion tokens
- agent cost share
- throughput/latency/cost vs concurrency when multiple runs supplied

Do not manually type chart values.

## Recalculation

Provide a path to load an existing persisted run and apply a different cost profile/calculator version without rerunning inference or modifying raw observations.

## Focused tests

Required deterministic-fixture coverage:

1. raw observations persist and load correctly
2. run/request/execution hierarchy relationships preserved
3. failed requests remain stored
4. PostgreSQL and Parquet required fields agree/round-trip
5. total run cost formula correct
6. attributed request costs sum to total attributable pool
7. agent cost shares sum correctly
8. concurrent GPU attribution does not double-count overlapping wall time
9. changing cost profile recalculates without mutating raw observations
10. cost/query percentiles correct
11. agent/tool latency and cost aggregations correct
12. cumulative fan-out work distinguished from critical-path stage latency
13. generated reports/charts read persisted data rather than live workflow objects

Run focused Phase 7 tests only; final full validation occurs in Phase 8.

## Acceptance criteria

Phase 7 is complete when raw experiment history is durable in PostgreSQL and portable Parquet, assignment cost metrics are reproducibly derived through versioned calculators, cost attribution satisfies accounting invariants without concurrency double-counting, historical runs can be recalculated, and dashboards/reports can compare runs and agents.

## Out of scope

- cloud provisioning
- final GPU benchmark execution
- arbitrary model-serving redesign