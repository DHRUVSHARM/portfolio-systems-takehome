# Experiment Results

This file is the canonical record of successful experiments included in the
submission. All listed benchmark numbers were checked against local artifacts
under `results/phase8`.

`canonical_100` below means the benchmark dataset mode. The runs are still
noncanonical CPU runs using `Qwen/Qwen3-0.6B`, not canonical GPU performance
measurements.

## Included Runs

| Run ID | Dataset | Concurrency | Model | Result | Purpose |
| --- | --- | --- | --- | --- | --- |
| `NONCANONICAL_CPU_INTEGRATION_SINGLE` | one request | 1 | `Qwen/Qwen3-0.6B` | completed manually | Verify workflow, Jaeger trace, correlation IDs, inference tags, and actual API response |
| `NONCANONICAL_CPU_INTEGRATION-20260828T205107Z` | `sampled_10`, seed 81 | 2 | `Qwen/Qwen3-0.6B` | 10 successful, 0 failed | Validate benchmark -> telemetry -> analytics -> report pipeline |
| `NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z` | `canonical_100` | 2 | `Qwen/Qwen3-0.6B` | 100 successful, 0 failed | Required 100-query CPU baseline |

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

Jaeger search:

```json
{"run_id":"NONCANONICAL_CPU_INTEGRATION_SINGLE","request_id":"cpu-rehearsal-single"}
```

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
- Inference observations: 10 requests, 2,656 prompt tokens, 1,310 completion
  tokens, 3,966 total tokens.
- vLLM max running requests: `2`.
- vLLM max waiting requests: `0`.
- vLLM max preemptions: `0`.

Artifacts:

| Type | Path |
| --- | --- |
| Raw | `results/phase8/raw/NONCANONICAL_CPU_INTEGRATION-20260828T205107Z/` |
| Telemetry | `results/phase8/telemetry/NONCANONICAL_CPU_INTEGRATION-20260828T205107Z/` |
| Analytics | `results/phase8/analytics/NONCANONICAL_CPU_INTEGRATION-20260828T205107Z/` |
| Report | `results/phase8/analytics/NONCANONICAL_CPU_INTEGRATION-20260828T205107Z/report/index.html` |

## Required 100-Query C2 Baseline

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
- Inference observations: 100 requests, 25,988 prompt tokens, 12,089
  completion tokens, 38,077 total tokens.
- vLLM max running requests: `2`.
- vLLM max waiting requests: `0`.
- vLLM max preemptions: `0`.
- vLLM generation throughput max observed: `13.40 tokens/s`.

Artifacts:

| Type | Path |
| --- | --- |
| Raw | `results/phase8/raw/NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z/` |
| Telemetry | `results/phase8/telemetry/NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z/` |
| Analytics | `results/phase8/analytics/NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z/` |
| Report | `results/phase8/analytics/NONCANONICAL_CPU_CANONICAL_100_C2-20260828T213917Z/report/index.html` |

## Excluded C16 Artifact

The only discovered C16 canonical_100 local artifact is:

```text
NONCANONICAL_CPU_CANONICAL_100_C16-20260828T235431Z
```

Its artifacts parse and the report exists, but `report.json` records 100
attempted requests, 0 successful requests, and 100 failures. It is therefore not
included as successful evidence and should not be used to claim throughput or
latency improvement.

## Raw Generated Advice

Actual model-generated Advisor output is stored in each successful raw run:

```text
results/phase8/raw/<RUN_ID>/requests.jsonl
```

Each successful row contains `response_body.summary`.
