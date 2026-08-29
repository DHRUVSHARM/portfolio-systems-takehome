# Phase 6: Deep Observability

## Goal

Add correlated live visibility across Gateway, Portfolio workflow, agent/tool/ticker execution, inference and physical resources without changing workload semantics or making observability a request-path dependency.

## Architecture

```text
METRICS
Gateway /metrics
Portfolio /metrics
vLLM /metrics
node-exporter
cAdvisor
DCGM exporter (GPU only)
    -> Prometheus

TRACES
Gateway + Portfolio + Advisor inference boundary
    -> OpenTelemetry
    -> OTel Collector
    -> Jaeger

LOGS
structured JSON stdout
    -> Grafana Alloy
    -> Loki

UI
Prometheus + Jaeger + Loki
    -> Grafana

ALERTS
Prometheus rules
    -> Alertmanager
```

## Prerequisites

- Phases 1-5 complete.
- Benchmark/request identities stable.
- Read `docs/architecture/system_contracts.md`.

## Cardinality rule

Prometheus is aggregate-only. Never use these as Prometheus labels:

- request_id
- query_id
- run_id
- trace_id
- arbitrary user IDs
- per-request identifiers
- ticker

Use bounded labels such as service, agent, tool, stage, status, HTTP code and bounded error type.

Exact per-request/per-ticker detail belongs in Jaeger, logs and Phase 7 historical observations.

## Gateway metrics

Expose low-cardinality metrics for at least:

- requests total/rate by status class/code as appropriate
- request duration histogram
- active/inflight
- queued/waiting
- queue wait duration
- admission rejection count
- queue timeout count
- downstream duration
- downstream failures

Use seconds as Prometheus timing base units.

## Portfolio metrics

Expose aggregate metrics for:

- active/total workflows
- agent call count/duration/errors by agent
- tool call count/duration/errors by tool/agent
- metric tasks running/waiting if measurable
- workflow CPU slots used/waiting if measurable

Agents must be individually visible: MetricsAgent, PriceAgent, RiskAgent, AdvisorAgent. Do not add ticker labels.

## Trace hierarchy

Propagate W3C trace context through benchmark/client -> Gateway -> Portfolio -> workflow -> Advisor HTTP request.

One request should be representable as:

```text
POST /v1/analyze
├ gateway.validation
├ gateway.admission_wait
└ portfolio.request
  ├ MetricsAgent[AAPL]
  │ └ PriceAgent.get_history[AAPL]
  ├ MetricsAgent[MSFT]
  │ └ PriceAgent.get_history[MSFT]
  ├ ...
  ├ RiskAgent.assess
  └ AdvisorAgent.summarize
    └ inference.request
```

Useful span attributes include run/request/query IDs, stage, agent, tool, ticker, n_holdings, lookback, model, token counts and queue wait where applicable. Do not include secrets. Do not record full prompts by default.

## Thread context propagation

Metrics/Risk execute in Phase 1 worker threads. Explicitly preserve OpenTelemetry parent context when dispatching work into the shared executor so agent/tool spans remain children of the correct portfolio request. Test this rather than assuming library magic.

## vLLM metrics

Scrape vLLM's native `/metrics` directly. Do not duplicate native scheduler/cache engine metrics in application code.

Dashboards should accommodate relevant metrics available in the pinned vLLM version, including where available:

- prompt/generation token counters/distributions
- running/waiting requests
- queue time/reason
- TTFT
- inter-token latency / time per output token
- prefill/decode/inference timing
- KV cache utilization
- prefix cache queries/hits/cached tokens
- preemptions

Version-dependent fields should fail gracefully rather than breaking the stack.

Detailed vLLM tracing is configurable and OFF for canonical benchmarks unless deliberately enabled because it may add overhead.

## Structured logs

Applications log JSON to stdout. Include useful fields when available:

- timestamp/level/service/event
- trace_id/span_id
- run_id/request_id/query_id
- stage/agent/tool/ticker
- duration/status/error type

Do not log API keys, credentials or full prompts by default.

Grafana Alloy collects Docker stdout/stderr and sends to Loki. Use low-cardinality Loki labels such as service/environment/level/container; IDs stay JSON fields rather than indexed labels.

## Observability configuration tree

Prepare configuration in Git, e.g.:

```text
observability/
  prometheus/
    prometheus.yml
    rules/
  alertmanager/
    alertmanager.yml
  otel/
    collector.yaml
  loki/
    loki.yaml
  alloy/
    config.alloy
  grafana/
    provisioning/
    dashboards/
```

Phase 8 wires these into Compose. Configuration should already be syntactically checkable where tooling allows.

## Grafana dashboards

Provision dashboards from files, not manual-only UI state.

### 1. System Overview

Show RPS, success/error, E2E p50/p95/p99, Gateway inflight/queue, Portfolio active workflows, vLLM running/waiting, token throughput, host CPU/RAM and GPU utilization/VRAM when present.

### 2. Gateway

Show inflight, queued, rejections, queue wait, status codes, downstream latency/timeouts.

### 3. Agent / Workflow Breakdown

Required dashboard. Show:

- agent call volume
- per-agent p50/p95/p99 latency
- per-agent error rate
- tool call volume/latency
- Metrics fan-out activity
- CPU worker saturation
- Risk latency
- Advisor latency
- stage latency contribution/critical path where the metrics permit it

Prometheus provides aggregate views; exact request drill-down links to Jaeger.

### 4. vLLM Inference

Show running/waiting, TTFT, queue/prefill/decode, prompt/completion tokens, tokens/sec, KV utilization, prefix-cache behavior, preemptions and GPU metrics when available.

### 5. Resources

Show host CPU/RAM/swap/load/disk/network, per-container CPU/RAM/network, and GPU utilization/VRAM/power/temp/energy when available.

### 6. Live Benchmark

Show current run traffic/result counters if exported safely, RPS/p95/errors, Gateway queue, vLLM queue, token throughput, GPU and KV pressure.

Historical run/cost comparison belongs to Phase 7.

## Resource exporters

Prepare/use node-exporter and cAdvisor in all deployment profiles. Prepare DCGM exporter for GPU deployment only. Local CPU deployment must operate correctly when DCGM is absent.

## Alerts

Provision sensible Prometheus rules and Alertmanager integration for:

- GatewayDown
- PortfolioDown
- VLLMUnavailable
- HighP95Latency
- HighErrorRate
- GatewayQueueGrowing
- GatewayHighRejectionRate
- WorkflowWorkersSaturated
- InferenceQueueGrowing
- HighKVCacheUsage
- InferencePreemptions
- HostMemoryPressure
- HighCPUUtilization
- GPUHighMemoryUsage / GPUHighTemperature when GPU metrics exist

Use `for:` windows to avoid alerting on one transient sample. A simple Alertmanager UI/receiver is enough; no PagerDuty required.

## Failure isolation

Telemetry backend failure must not fail serving. If OTel collector, Jaeger, Prometheus, Loki or Grafana is unavailable, application requests continue. Instrumentation/export exceptions should fail safely.

Tracing sample ratio must be configurable. Canonical 100 may use 100% application traces; extreme stress may reduce sampling to control overhead. Exact benchmark raw observations still capture all requests.

## Focused tests

Automated tests should validate instrumentation semantics rather than attempt browser UI testing:

1. Prometheus labels contain no request/run/query/trace IDs or ticker
2. Gateway metrics update correctly
3. agent metrics distinguish all agents
4. tool metrics distinguish tools
5. trace structure contains Gateway -> Portfolio -> per-ticker fanout -> Risk -> Advisor -> inference boundary
6. executor-thread spans retain correct parent context
7. RequestContext IDs appear as trace/log fields
8. trace IDs appear in structured logs
9. telemetry export failure does not fail workflow request
10. sampling configuration respected
11. observability config files validate syntactically where practical

## Checkpoint 2

After focused tests, run the full Python test suite once and validate the complete application behavior. This is Checkpoint 2. Perform Git review here before implementing cost analytics.

## Acceptance criteria

Phase 6 is complete when live aggregate metrics, request-level traces and correlated logs cover Gateway/workflow/inference boundaries, per-agent/tool behavior is visible, worker-thread trace parentage is correct, vLLM/resource exporters are prepared, dashboards/alerts are provisioned, and observability failure cannot break serving.

## Out of scope

- authoritative historical PostgreSQL analytics
- exact cost attribution
- final cloud GPU benchmark execution