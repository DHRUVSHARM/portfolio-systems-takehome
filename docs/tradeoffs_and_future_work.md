# Tradeoffs And Future Work

## Design Decisions And Tradeoffs

### CPU Rehearsal vs Canonical GPU

- Decision: keep a local CPU rehearsal path beside the canonical GPU path.
- Why: it validates integration, telemetry, analytics, and reports without requiring cloud GPU time for every iteration.
- Tradeoff: CPU timings and the small model are not representative GPU performance.
- Extend: run the canonical benchmark on the target GPU host with real provider cost and DCGM telemetry.

### Qwen3-0.6B Local Model vs Qwen3-4B GPU Target

- Decision: use `Qwen/Qwen3-0.6B` locally and reserve `Qwen/Qwen3-4B-Instruct-2507` for the GPU target.
- Why: the smaller model makes CPU rehearsal practical.
- Tradeoff: generated text quality and latency differ from the canonical model.
- Extend: freeze the exact 4B revision and rerun the same benchmark modes on GPU.

### vLLM Instead Of Direct Transformers Inference

- Decision: serve inference through vLLM's OpenAI-compatible API.
- Why: it exposes scheduler, batching, KV-cache, and token metrics that matter for systems benchmarking.
- Tradeoff: local CPU vLLM setup is heavier than an in-process model call.
- Extend: tune vLLM serving parameters on the actual GPU host.

### Gateway Boundary And Admission Control

- Decision: put public validation, queueing, and saturation behavior in Gateway.
- Why: it separates client-facing overload handling from Portfolio workflow execution.
- Tradeoff: one more service boundary and downstream HTTP hop.
- Extend: calibrate max in-flight and queue limits against GPU saturation tests.

### Coordinated Timeout Budgets

- Decision: keep benchmark, Gateway downstream, and Portfolio inference timeouts independently configurable.
- Why: each boundary has a distinct failure and ownership domain, and explicit budgets make timeout behavior observable rather than implicit.
- Tradeoff: slow local CPU inference can outlive an upstream timeout when those values are not calibrated together for the offered load.
- Extend: define an end-to-end service-level objective on the GPU target and derive consistent client, Gateway, queue, and inference timeout budgets from it.

### Single Advisor Inference Call

- Decision: preserve exactly one Advisor inference call per successful workflow.
- Why: it keeps the supplied workload semantics clear and cost attribution traceable.
- Tradeoff: no speculative or multi-call reasoning experiments.
- Extend: only compare alternative Advisor strategies as explicit future experiments.

### Prometheus Low-Cardinality Labels

- Decision: keep request, query, run, and ticker IDs out of Prometheus labels.
- Why: Prometheus is best for aggregate operational time series.
- Tradeoff: exact request drilldown uses Jaeger/Loki/PostgreSQL rather than Prometheus label filters.
- Extend: add carefully bounded aggregate labels only when they answer an operational question.

### Traces And Logs For Request-Level IDs

- Decision: put `run_id`, `request_id`, and `query_id` in spans and JSON logs.
- Why: traces/logs are built for high-cardinality request debugging.
- Tradeoff: live retention windows may expire.
- Extend: lengthen trace/log retention for production review windows.

### Correlation IDs

- Decision: use `run_id`, `request_id`, and `query_id` throughout benchmark, Gateway, Portfolio API, runtime, inference, logs, and analytics.
- Why: each ID answers a different question: experiment, execution, benchmark input.
- Tradeoff: callers and collectors must preserve the mapping carefully.
- Extend: add a lightweight artifact validator for ID completeness.

### Parquet And PostgreSQL Historical Storage

- Decision: write immutable Parquet artifacts and load PostgreSQL analytics.
- Why: Parquet is portable and versionable; PostgreSQL is queryable by Grafana.
- Tradeoff: two historical stores must stay schema-compatible.
- Extend: automate schema compatibility checks for every collected run.

### Frozen Telemetry Per Run

- Decision: export Jaeger and Prometheus samples into run artifacts.
- Why: historical analysis should not depend on live backend retention.
- Tradeoff: artifacts can be large.
- Extend: compress archived telemetry bundles.

### Static HTML Report Plus Grafana

- Decision: generate a portable HTML/SVG report in addition to Grafana.
- Why: reviewers can inspect one run without a live database/dashboard.
- Tradeoff: the static report is a snapshot, not an exploratory UI.
- Extend: add richer multi-run comparison reports.

### Synthetic CPU Cost Profile

- Decision: use `reference_cpu_demo` for local CPU accounting.
- Why: it makes the full cost pipeline testable without a real rented GPU bill.
- Tradeoff: costs are illustrative assumptions, not physical measurements.
- Extend: replace with provider hourly pricing for canonical GPU runs.

### CPU / Inference / Overhead Pools

- Decision: allocate total cost into CPU, inference, and overhead pools.
- Why: it prevents every dollar from being forced onto agent spans and keeps shared infrastructure explicit.
- Tradeoff: pool fractions are configurable assumptions.
- Extend: calibrate pools from measured GPU/CPU utilization and provider pricing.

### Completion-Token Weighting

- Decision: weight completion tokens more heavily than prompt tokens in the CPU demo profile.
- Why: autoregressive decoding usually has different serving cost from prefill.
- Tradeoff: the `4x` decode weight is a heuristic, not a universal constant.
- Extend: calibrate prefill/decode weights from vLLM/GPU telemetry.

### Exclusive Nested-Span Work

- Decision: subtract child span work from parent spans for CPU-side attribution.
- Why: nested traces would otherwise double-count work.
- Tradeoff: trace completeness matters.
- Extend: collect more precise per-span CPU seconds.

### Trace-Level Request ID Recovery

- Decision: let the collector recover request identity at trace level for child spans that do not repeat every request tag.
- Why: PriceAgent/MetricsAgent/RiskAgent work should still participate in analytics and cost attribution.
- Tradeoff: ambiguous traces must be handled conservatively.
- Extend: add stronger trace-shape validation for production runs.

### CPU Wall-Time Proxy

- Decision: use exclusive wall time as a fallback when CPU time is absent.
- Why: it avoids losing CPU-side attribution in local traces.
- Tradeoff: wall time is not the same as measured CPU seconds.
- Extend: capture process/thread CPU time consistently across agents.

### Unavailable Metrics As N/A

- Decision: render unavailable metrics as N/A rather than zero.
- Why: zero is a measurement; unavailable is absence of evidence.
- Tradeoff: dashboards need clear empty-state handling.
- Extend: add per-run telemetry completeness scoring.

### Local WSL Exporter Limitations

- Decision: let CPU rehearsal run without host exporters or DCGM.
- Why: local WSL environments often cannot expose the same host/GPU surfaces.
- Tradeoff: resource panels may be partially empty locally.
- Extend: validate full exporter coverage on the GPU host.

### Local CPU Load-Test Scope

- Decision: use the successful concurrency-2 100-query run as the reproducible CPU submission baseline and reserve capacity characterization for appropriate GPU hardware.
- Why: the local CPU path is designed to validate the complete system, observability, analytics, and cost pipeline rather than establish production serving limits.
- Tradeoff: the submission does not claim a CPU saturation point or extrapolate local throughput to GPU performance.
- Extend: run a controlled concurrency sweep on the canonical GPU target with coordinated timeout budgets and explicit success, queueing, latency, and saturation criteria.

## Future Work

- Run the canonical GPU benchmark with `Qwen/Qwen3-4B-Instruct-2507`.
- Record actual provider hourly pricing in a real GPU cost profile.
- Use DCGM energy data for energy-grounded attribution.
- Capture measured CPU seconds consistently for all workflow spans.
- Calibrate prefill/decode token weights from serving telemetry.
- Define an end-to-end timeout budget from the target service-level objective.
- Run a controlled concurrency sweep on the GPU target.
- Run saturation/load tests on appropriate GPU hardware.
- Compare prefix-cache enabled vs disabled serving profiles.
- Compare larger or alternative open-weight models.
- Add automated multi-run comparison reports.
- Add experiment regression dashboards.
- Extend trace/log retention for longer review windows.
- Consider distributed or multi-GPU serving only if scale requires it.

These are production hardening and further experimentation paths, not missing requirements for the current submission.
