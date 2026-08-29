# Phase 8: Docker Compose Deployment and Final Experiments

## Goal

Integrate the completed application, benchmark, observability and analytics layers into one reproducible Docker Compose deployment that works locally on CPU and on a single NVIDIA GPU VM, then execute the final benchmark/tuning plan and produce assignment-ready results.

## Prerequisites

- Phases 1-7 complete.
- Checkpoints 1 and 2 reviewed.
- Read `docs/architecture/system_contracts.md`.
- Do not redesign application APIs or observability semantics in this phase.

## Deployment principle

Docker Compose is the only required executable deployment mechanism. Do not introduce Kubernetes into the runtime path.

Use a common stack plus environment-specific overrides:

```text
deploy/compose/
  compose.common.yaml
  compose.cpu.yaml
  compose.gpu.yaml
  .env.example
```

CPU and GPU profiles must preserve the same service names and application interfaces. Only inference/runtime-specific details should change.

## Common stack

Integrate services as implemented by previous phases:

- gateway
- portfolio
- postgres
- prometheus
- grafana
- alertmanager
- otel-collector
- jaeger
- loki
- alloy
- node-exporter
- cadvisor
- optional benchmark-runner profile

Add Redis only if the implemented runtime actually uses it. Do not include infrastructure purely for decoration.

## vLLM service

CPU override: use an appropriate vLLM CPU runtime for local integration/smoke tests. A smaller model may be allowed only for explicitly noncanonical smoke tests if the canonical 4B model is impractical locally.

GPU override: use vLLM CUDA runtime with exactly one NVIDIA GPU reservation.

Canonical final model:

`Qwen/Qwen3-4B-Instruct-2507`

Canonical baseline dtype:

`bfloat16` / BF16 where supported.

Application Advisor endpoint remains logically:

`http://vllm:8000/v1`

No application code branch should care whether the server runs on CPU or CUDA.

## Inference configuration profiles

Create versioned external profiles, e.g.:

```text
configs/inference/
  local-cpu.yaml
  cloud-gpu-baseline.yaml
  cloud-gpu-<experiment>.yaml
```

Capture at least:

- model and pinned revision if used
- vLLM version/image
- dtype
- max_model_len
- max_num_seqs
- gpu_memory_utilization
- prefix-caching state
- max_num_batched_tokens when explicitly tuned

Advisor generation remains fixed by Phase 2: temperature 0, max_tokens 256, retry_count 0.

## Context-length selection

Before freezing the canonical max_model_len, measure actual Advisor prompt/token sizes across the full 1000-query corpus using the completed prompt builder/token evidence where possible. Choose workload-justified headroom rather than blindly using the model's theoretical maximum. Record the chosen rationale in run metadata/documentation.

## Networking and exposure

Use private Compose networks and stable service DNS names. vLLM, PostgreSQL, Loki and OTel receivers should not be publicly exposed by default.

Host-accessible endpoints should be intentional, typically Gateway and Grafana; Prometheus/Jaeger may be bound to localhost for debugging. For the cloud canonical benchmark, SSH/SSH tunneling is sufficient. TLS is not required between private Compose services.

## Health and readiness

Configure meaningful checks:

- PostgreSQL readiness via pg readiness
- vLLM/model readiness
- Portfolio `/ready`
- Gateway `/ready`

Gateway/Portfolio serving must not depend on Grafana, Jaeger, Loki or other observability UIs being healthy.

## Persistent volumes

Persist as appropriate:

- PostgreSQL data
- model/Hugging Face cache
- result artifacts
- Grafana provisioning/state if needed
- Prometheus/Loki storage when useful for experiment inspection

The critical requirements are that model weights are cached before measured runs and benchmark results survive container teardown/restart.

## Model startup and warmup

Canonical steady-state benchmark procedure:

```text
prepare/download model
-> start stack
-> wait for vLLM model-ready
-> verify Gateway/Portfolio readiness
-> issue explicit warmup requests
-> mark/exclude warmup from measurement
-> begin measured run
```

Do not include model download or cold startup in steady-state query latency/cost distributions. Startup/model-load time may be reported separately.

## Configuration snapshot and provenance

Every canonical run must persist a self-describing snapshot including where available:

- run ID/name
- git commit
- resolved application/benchmark/cost configuration
- resolved Compose configuration
- container image tags/digests
- model and model revision
- vLLM version
- dtype
- max_model_len/max_num_seqs/max_num_batched_tokens/gpu_memory_utilization/prefix caching
- GPU model/VRAM
- driver/CUDA versions
- CPU/RAM
- Gateway and workflow concurrency configuration
- dataset/manifest and client concurrency
- actual cost profile/hourly rate
- start/end/duration/status

Do not include secrets.

## Secrets

Commit only `.env.example`. Never commit `.env`, cloud credentials, API keys or passwords. Do not write secrets into run.json, metrics, logs or traces.

## GPU telemetry

GPU profile enables DCGM exporter and associated Prometheus/Grafana panels. CPU profile must work cleanly with no DCGM target required.

## Benchmark runner

Provide an optional Compose profile/service for the Phase 5 runner. Its own CPU/RAM must be visible through cAdvisor so the load generator cannot silently become the bottleneck. Bound its resources if useful.

For canonical same-host tests, benchmark-runner -> Gateway may use the private network to reduce external network noise. Separate external-edge/gateway tests may run from another host if needed.

## Gateway-only stress experiment

Using a cheap stub/fake Portfolio downstream, separately demonstrate Gateway high-connection behavior up to roughly 1000 clients as practical. Measure bounded active requests, bounded queue, controlled 503 rejection, memory/CPU behavior and recovery after load.

Do not call this the canonical model-inference benchmark; it tests Gateway capacity independently.

## Canonical experiment sequence

Run controlled experiments; change one meaningful serving dimension at a time and preserve all resolved configs/results.

### A. Baseline concurrency sweep

Hold model, prompt, generation settings, dtype, context length and vLLM baseline configuration fixed. Run `canonical_100` across a useful concurrency series such as:

```text
1, 2, 4, 8, 16, 32, 64
```

Continue beyond 64 only if the system has not clearly saturated and the additional point is useful. Do not generate arbitrary large sweeps merely for more charts.

Capture per run:

- success/failure
- throughput
- E2E p50/p95/p99
- Gateway queue wait/rejections
- workflow/agent latency
- Advisor/inference latency
- TTFT
- prompt/completion/token throughput
- vLLM running/waiting
- KV utilization
- preemptions
- GPU utilization/VRAM/power/energy where available
- total run cost
- cost/query distribution
- agent cost shares

Use these to identify the saturation knee rather than declaring the highest throughput point automatically best.

### B. max_num_seqs experiment

Choose a representative client load near the useful concurrency region. Compare a small, justified set of `max_num_seqs` values. Explain impact on throughput, TTFT, queueing, KV pressure, preemption and cost/query. Do not sweep dozens of values.

### C. Prefix caching experiment

Compare prefix caching OFF vs ON with otherwise identical configuration. Use native vLLM prefix-cache metrics to determine whether the workload actually reuses enough prefix tokens to benefit. Report no meaningful benefit if that is what the data shows.

### D. max_num_batched_tokens experiment

Run only if baseline evidence indicates prefill scheduling/batching is a worthwhile bottleneck to tune. Otherwise leave it at baseline/default and explicitly document that the experiment was not justified.

### E. Full 1000-query final run

After selecting the final/tuned serving profile, run `full_1000`. Use it for robust distributions and workload-shape relationships:

- holdings count vs latency/cost
- prompt/completion token relationships
- tail latency
- cost distribution
- agent/tool breakdown
- resource efficiency

## Separate vLLM microbenchmark

Where useful, run vLLM's own serving benchmark directly against the model server. Label results as vLLM-only microbenchmark. Never mix these latencies with end-to-end Gateway benchmark latencies.

## Cost measurement boundary

For the rented cloud VM, use the actual current hourly machine rate recorded in the run's cost profile. Measure the experiment window explicitly. If the provider price bundles GPU/CPU/RAM, use that rate as the authoritative infrastructure-cost source and let Phase 7 attribution allocate the pool.

Warmup/model download are excluded from steady-state per-query experiment metrics unless a separate startup-cost analysis is intentionally reported.

## Final reports

Generate final assignment outputs from persisted observations/calculators, including at minimum:

- total cost
- cost per query and percentiles
- percentage cost per agent
- cost-per-query histogram/box/CDF
- throughput vs concurrency
- p95/p99 vs concurrency
- TTFT and inference queue behavior
- token throughput
- KV utilization/preemptions
- GPU utilization/VRAM/power where available
- per-agent latency and cost breakdown
- resource efficiency and saturation-knee interpretation

Prefer clear evidence-based conclusions over maximizing the number of experiments.

## Final validation

At the final checkpoint perform exactly the comprehensive validation needed for submission:

1. full Python test suite
2. Compose config validation
3. local/CPU stack smoke where practical
4. health/readiness checks
5. one E2E request through Gateway -> Portfolio -> Advisor -> inference server
6. Prometheus target validation
7. one trace showing per-ticker fanout/barrier/Risk/Advisor/inference hierarchy
8. correlated structured logs
9. Grafana datasources/dashboard provisioning available
10. PostgreSQL historical persistence
11. Parquet export/readback
12. cost-accounting invariants
13. canonical cloud GPU benchmark and selected experiments

Do not repeatedly rerun the complete suite during intermediate Phase 8 edits unless a failure requires it.

## Acceptance criteria

Phase 8 is complete when a fresh compatible host can launch the documented Compose stack, CPU/GPU profiles keep application interfaces unchanged, model/config/provenance are reproducible, canonical benchmark/warmup boundaries are explicit, observability and historical analytics work together, actual GPU experiments produce the required assignment cost metrics, and final reports/charts are generated from persisted observations.

## Explicitly out of scope

- Kubernetes as required runtime
- multi-GPU tensor parallelism
- speculative decoding unless a concrete late finding justifies it
- LoRA/RAG/multi-model serving
- manual application batching
- changing portfolio workflow semantics
- yfinance during canonical benchmark
- silent inference retries/fallbacks