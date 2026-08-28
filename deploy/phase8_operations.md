# Phase 8 Operations

Phase 8 provides deployment, integration rehearsal, and experiment tooling. It
does not include final GPU measurements or final submission narrative.

## CPU Rehearsal

Purpose: complete noncanonical integration validation on the CPU stack.

Use:

- common Compose plus CPU Compose
- canonical Qwen model if practical, or a smaller compatible Qwen model by
  configuration only
- `configs/cost/reference_cpu_demo.yaml`
- tiny sampled benchmark only
- automatic Jaeger and Prometheus export
- Postgres persistence
- Parquet, reports, and chart data
- Grafana historical panels

The CPU rehearsal is explicitly `NONCANONICAL` and uses `SYNTHETIC COST`.
Never mix these cost or latency numbers into canonical GPU comparisons.

```bash
cp deploy/compose/.env.example deploy/compose/.env
bash deploy/experiments/phase8_cpu_rehearsal.sh rehearsal
```

The script performs:

1. build/pull required images
2. start common + CPU services
3. wait for CPU vLLM `/v1/models`
4. verify expected served model
5. wait for Portfolio `/ready`
6. wait for Gateway `/ready`
7. measure context through the deployment-tools container
8. send one real business request
9. run a small `NONCANONICAL_CPU_INTEGRATION` benchmark
10. export Jaeger traces for the run window
11. export Prometheus CPU/vLLM/host/container telemetry
12. run the Phase 7 collector inside Compose
13. persist raw and cost analysis to PostgreSQL
14. generate Parquet, reports, and charts
15. verify non-zero cost, query-type breakdown, holdings breakdown, and report
    accounting artifacts

CPU mode does not require DCGM, GPU utilization, GPU VRAM, GPU power, or a DCGM
Prometheus target.

## GPU Canonical

Purpose: real assignment measurements on a single NVIDIA GPU host.

Flow:

```text
provider setup
-> actual hourly rate
-> immutable model revision
-> model download/cache
-> context measurement
-> vLLM metric preflight
-> stack readiness
-> warmup
-> measured canonical_100
-> automatic required telemetry
-> analytics/cost/report
-> tuning
-> final full_1000
```

Before measured GPU work, set in `deploy/compose/.env` or the shell:

```bash
PROVIDER_NAME=<real-provider>
PROVIDER_INSTANCE_TYPE=<real-gpu-instance>
MACHINE_HOURLY_USD=<actual-bundled-vm-hourly-rate>
VLLM_MAX_MODEL_LEN=<measured-or-larger-context-length>
```

The example values `PROVIDER_NAME=local` and `MACHINE_HOURLY_USD=0` are
deliberate dry-run placeholders. Canonical GPU scripts reject them.

Resolve and export the immutable model revision:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh resolve_revision
export VLLM_MODEL_REVISION=<resolved_revision_from_results/phase8/model-revision/qwen3_4b_instruct_2507.json>
```

Measure canonical context length through the pinned deployment-tools image:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh measure_context
```

The context result is written to:

```text
results/phase8/context/context_lengths_canonical_100.json
```

Canonical measured runs require `status: measured`, and
`VLLM_MAX_MODEL_LEN` must be greater than or equal to the recommendation.

Start and verify the GPU stack:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh start_stack
```

Run warmup traffic outside the measured results:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh warmup
```

Run the canonical 100-query tuning sweep:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh canonical_sweep
```

Each measured run automatically performs:

1. preflight checks for NVIDIA, immutable revision, measured context, real cost,
   inference profile/runtime match, vLLM readiness, served model, Gateway
   readiness, Jaeger, Prometheus, and vLLM metrics
2. benchmark raw artifact generation
3. required Jaeger trace export into `results/phase8/telemetry/<run_id>/`
4. required Prometheus range export with GPU and vLLM telemetry
5. collector execution with `--jaeger-trace-json`,
   `--prometheus-samples-json`, and `--require-telemetry`
6. PostgreSQL persistence
7. Parquet, report, and chart generation

If telemetry export fails, raw benchmark artifacts remain in
`results/phase8/raw/<run_id>/` and a visible incomplete marker is written under
`results/phase8/telemetry/<run_id>/`. The collector refuses to emit complete
analytics when required telemetry is missing or empty.

Run prefix-cache comparison only after selecting the comparison concurrency:

```bash
export PREFIX_CACHE_CONCURRENCY=<chosen_from_canonical_100_tuning>
bash deploy/experiments/phase8_gpu_experiments.sh prefix_cache_comparison
```

The prefix OFF run records `configs/inference/cloud-gpu-baseline.yaml`.
The prefix ON run records `configs/inference/cloud-gpu-prefix-cache.yaml`.
Only prefix caching should differ.

Run final full_1000 only after tuning selects the final concurrency:

```bash
export FULL_1000_CONCURRENCY=<chosen_from_tuning>
bash deploy/experiments/phase8_gpu_experiments.sh full_1000_final
```

There is no default full_1000 concurrency.

## Model Revision

Resolve an immutable Hugging Face commit SHA:

```bash
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.gpu.yaml run --rm deployment-tools \
  python -m portfolio.portfolio.deployment.model_revision \
    --model Qwen/Qwen3-4B-Instruct-2507 \
    --output /results/phase8/model-revision/qwen3_4b_instruct_2507.json
```

Canonical GPU runs must set `VLLM_MODEL_REVISION` to the resolved 40-character
commit SHA. CPU local smoke may leave revision unset only when clearly labeled
noncanonical.

## vLLM Metrics

Run preflight against the private vLLM service from the tools container:

```bash
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.gpu.yaml run --rm deployment-tools \
  python -m portfolio.portfolio.deployment.telemetry vllm-preflight \
    --vllm-base-url http://vllm:8000 \
    --output /results/phase8/telemetry/vllm_preflight.json
```

Missing vLLM metrics are reported as unavailable/null and are not interpreted as
zero. Aggregate vLLM Prometheus histograms stay run-level telemetry, not exact
per-request fields.

## Gateway Stress

Gateway-only stress uses the fake Portfolio override and does not call Qwen or
vLLM:

```bash
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.gateway-stress.yaml up -d

python3 -m portfolio.portfolio.benchmark \
  --gateway-base-url http://localhost:8000 \
  --dataset-mode full_1000 \
  --concurrency 1000 \
  --request-timeout-seconds 30 \
  --run-name gateway-only-stress \
  --output-root results/gateway-stress
```

This validates Gateway admission behavior only and is not canonical inference
evidence.

## Demo Command Helpers

Ergonomic live-demo command helpers are prepared in:

```text
deploy/experiments/phase8_demo_commands.sh
```

They provide `single_request`, `demo_100`, `demo_1000`, and `show_results`
commands. They intentionally do not contain final demo narrative or final
results.
