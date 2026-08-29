# Experiment Results

This file is the canonical record of successful experiments included in the
submission. Successful benchmark artifacts are committed under
[`submission_artifacts/`](../submission_artifacts/) so the evidence can be
reviewed without rerunning the local stack.

`canonical_100` below means the benchmark dataset mode. All benchmark runs here
are noncanonical CPU runs using `Qwen/Qwen3-0.6B`, not canonical GPU performance
measurements.

## Included Runs

| Run ID | Dataset | Concurrency | Model | Result | Evidence |
| --- | --- | --- | --- | --- | --- |
| `NONCANONICAL_CPU_CANONICAL_100_C2-20260829T020534Z` | `canonical_100` | 2 | `Qwen/Qwen3-0.6B` | **100 successful, 0 failed** | [recorded 100-query C2 artifacts](../submission_artifacts/canonical_100_c2_recorded/) |
| `NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z` | `canonical_100` | 2 | `Qwen/Qwen3-0.6B` | **100 successful, 0 failed** | [earlier 100-query C2 artifacts](../submission_artifacts/canonical_100_c2/) |
| `NONCANONICAL_CPU_INTEGRATION-20260828T205107Z` | `sampled_10`, seed 81 | 2 | `Qwen/Qwen3-0.6B` | 10 successful, 0 failed | [10-query artifacts](../submission_artifacts/sampled_10/) |
| `NONCANONICAL_CPU_INTEGRATION_SINGLE` | one request | 1 | `Qwen/Qwen3-0.6B` | completed manually | [single-request validation](../submission_artifacts/single_request/) |

The newer C2 run is the primary reviewer evidence because it is the exact run
shown in the first two submission videos. The earlier successful C2 run is kept
as independent repeatability evidence.

## Video-Matched 100-Query C2 Run

Run ID: `NONCANONICAL_CPU_CANONICAL_100_C2-20260829T020534Z`

Configuration:

- Dataset mode: `canonical_100`
- Concurrency: `2`
- Query count: `100`
- Request timeout: `300 s`
- Model: `Qwen/Qwen3-0.6B`
- Environment: noncanonical local CPU rehearsal

Verified observations:

- Requests: 100 attempted, **100 successful, 0 failed**.
- Success rate: `100%`.
- Total illustrative cost: `$0.375744`.
- Mean cost/query: `$0.003757`.
- Median cost/query: `$0.003052`.
- p95 cost/query: `$0.006657`.
- p99 cost/query: `$0.016569`.
- Inference span latency: p50 about `26.40 s`, p95 about `40.53 s`.
- Inference observations: 101 spans, 26,268 prompt tokens, 12,220 completion tokens, 38,488 total tokens. The extra inference span corresponds to live/demo activity around the recorded run; the benchmark run itself contains exactly 100 requests and 100 successes.
- vLLM max running requests: `2`.
- vLLM max waiting requests: `0`.
- vLLM max preemptions: `0`.
- vLLM generation throughput max observed: about `12.59 tokens/s`.

Committed artifacts:

| Type | Path |
| --- | --- |
| Bundle | [`submission_artifacts/canonical_100_c2_recorded/`](../submission_artifacts/canonical_100_c2_recorded/) |
| Run identity/result | [`raw/run.json`](../submission_artifacts/canonical_100_c2_recorded/raw/run.json) |
| Raw responses | [`raw/requests.jsonl`](../submission_artifacts/canonical_100_c2_recorded/raw/requests.jsonl) |
| Resolved benchmark config | [`raw/resolved_benchmark_config.yaml`](../submission_artifacts/canonical_100_c2_recorded/raw/resolved_benchmark_config.yaml) |
| Frozen Jaeger/Prometheus telemetry | [`telemetry/`](../submission_artifacts/canonical_100_c2_recorded/telemetry/) |
| Derived metrics | [`analytics/metrics.json`](../submission_artifacts/canonical_100_c2_recorded/analytics/metrics.json) |
| Run summary | [`analytics/report.json`](../submission_artifacts/canonical_100_c2_recorded/analytics/report.json) |
| Serving telemetry | [`analytics/serving_telemetry.json`](../submission_artifacts/canonical_100_c2_recorded/analytics/serving_telemetry.json) |
| Static report | [`analytics/report/index.html`](../submission_artifacts/canonical_100_c2_recorded/analytics/report/index.html) |

The bundle also includes Parquet tables for request, execution, inference,
resource, and cost-attribution analysis.

## Earlier Verified 100-Query C2 Run

Run ID: `NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z`

Configuration:

- Dataset mode: `canonical_100`
- Concurrency: `2`
- Query count: `100`
- Model: `Qwen/Qwen3-0.6B`
- vLLM version: `0.8.5`
- max model length: `4096`
- max vLLM sequences: `32`

Verified observations:

- Requests: 100 attempted, 100 successful, 0 failed.
- Success rate: `100%`.
- Total illustrative cost: `$0.355583`.
- Mean cost/query: `$0.003556`.
- Median cost/query: `$0.002870`.
- p95 cost/query: `$0.006664`.
- p99 cost/query: `$0.014083`.
- Client latency: p50 `26.30 s`, p95 `38.02 s`, max `54.11 s`.
- Inference observations: 100 requests, 25,988 prompt tokens, 12,089 completion tokens, 38,077 total tokens.
- vLLM max running requests: `2`.
- vLLM max waiting requests: `0`.
- vLLM max preemptions: `0`.
- vLLM generation throughput max observed: `13.40 tokens/s`.

Committed artifacts:

| Type | Path |
| --- | --- |
| Bundle | [`submission_artifacts/canonical_100_c2/`](../submission_artifacts/canonical_100_c2/) |
| Raw responses | [`raw/requests.jsonl`](../submission_artifacts/canonical_100_c2/raw/requests.jsonl) |
| Frozen Jaeger/Prometheus telemetry | [`telemetry/`](../submission_artifacts/canonical_100_c2/telemetry/) |
| Derived metrics | [`analytics/metrics.json`](../submission_artifacts/canonical_100_c2/analytics/metrics.json) |
| Static report | [`analytics/report/index.html`](../submission_artifacts/canonical_100_c2/analytics/report/index.html) |

## 10-Query Rehearsal

Run ID: `NONCANONICAL_CPU_INTEGRATION-20260828T205107Z`

Configuration:

- Dataset mode: `sampled_10`
- Sample seed: `81`
- Concurrency: `2`
- Model: `Qwen/Qwen3-0.6B`
- vLLM version: `0.8.5`

Verified observations:

- Requests: 10 attempted, 10 successful, 0 failed.
- Total illustrative cost: `$0.037315`.
- Mean cost/query: `$0.003732`.
- p95 cost/query: `$0.007026`.
- Client latency: p50 `27.71 s`, p95 `32.77 s`.
- Inference observations: 10 requests, 2,656 prompt tokens, 1,310 completion tokens, 3,966 total tokens.
- vLLM max running requests: `2`.
- vLLM max waiting requests: `0`.
- vLLM max preemptions: `0`.

Committed artifacts:

- [10-query artifact bundle](../submission_artifacts/sampled_10/)
- [raw responses](../submission_artifacts/sampled_10/raw/requests.jsonl)
- [metrics](../submission_artifacts/sampled_10/analytics/metrics.json)
- [static report](../submission_artifacts/sampled_10/analytics/report/index.html)

## Single Request

Run ID: `NONCANONICAL_CPU_INTEGRATION_SINGLE`

Fixed identity:

| Field | Value |
| --- | --- |
| run ID | `NONCANONICAL_CPU_INTEGRATION_SINGLE` |
| request ID | `cpu-rehearsal-single` |
| query ID | `cpu-rehearsal-single` |

Purpose:

- Verify end-to-end Gateway -> Portfolio API -> workflow -> Advisor -> vLLM.
- Verify Jaeger trace propagation.
- Verify `run_id`, `request_id`, and `query_id` correlation.
- Verify inference token/span tags.
- Verify the API response contains model-generated Advisor output.

The validation details are preserved in
[`submission_artifacts/single_request/README.md`](../submission_artifacts/single_request/README.md).

## Local CPU Scope

The CPU environment is intentionally an integration and observability rehearsal,
not a capacity benchmark. CPU inference is substantially slower than the target
GPU deployment, and long-running requests pass through several independently
bounded timeout layers: benchmark client, Gateway downstream request, and
Portfolio inference. Higher-concurrency capacity testing should therefore be
performed on the canonical GPU environment with those limits calibrated together
for the expected service-level objective.

The submission uses concurrency 2 because both committed 100-query runs complete
the required workload cleanly and provide reproducible end-to-end datasets for
metrics, traces, cost attribution, raw model responses, and generated reports.

## Raw Generated Advice

Actual model-generated Advisor output for the video-matched 100-query run is:

[`submission_artifacts/canonical_100_c2_recorded/raw/requests.jsonl`](../submission_artifacts/canonical_100_c2_recorded/raw/requests.jsonl)

Each successful row contains the response body and generated Advisor summary.
