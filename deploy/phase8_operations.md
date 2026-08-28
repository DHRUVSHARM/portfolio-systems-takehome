# Phase 8 Operations

Phase 8 adds reproducible deployment, experiment, and post-run collection
infrastructure. It does not add final experiment results.

## Profiles

CPU smoke:

```bash
cp deploy/compose/.env.example deploy/compose/.env
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.cpu.yaml config
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.cpu.yaml up -d
```

GPU canonical:

```bash
cp deploy/compose/.env.example deploy/compose/.env
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.gpu.yaml config
docker compose --env-file deploy/compose/.env \
  -f deploy/compose/compose.common.yaml \
  -f deploy/compose/compose.gpu.yaml up -d
```

Only Gateway and Grafana are public by default. Prometheus and Jaeger are bound
to localhost. vLLM, PostgreSQL, Loki, OTel, Alloy, exporters, and Portfolio stay
on private Compose networks.

## Warmup Boundary

Use this order for measured runs:

1. Pull/build pinned images.
2. Populate the Hugging Face/model cache.
3. Start the stack.
4. Wait for vLLM `/v1/models`.
5. Wait for Portfolio `/ready`.
6. Wait for Gateway `/ready`.
7. Run warmup benchmark requests.
8. Mark warmup complete.
9. Run measured benchmark.
10. Collect traces, Prometheus range samples, raw request observations, Parquet,
    PostgreSQL persistence, costs, and reports.

Warmup requests are excluded from measured benchmark artifacts.

## Context Lengths

Run before canonical GPU results:

```bash
python3 -m portfolio.portfolio.deployment.context_lengths \
  --dataset-mode canonical_100 \
  --model Qwen/Qwen3-4B-Instruct-2507 \
  --generation-allowance 256 \
  --output results/phase8/context_lengths_canonical_100.json
```

The command builds actual Advisor prompts from the selected workload and uses
the real Qwen tokenizer. If `transformers` or the tokenizer cannot be loaded on
the local host, the output is marked `tokenizer_unavailable`; execute it on the
GPU host before canonical runs and update `VLLM_MAX_MODEL_LEN` from the reported
recommendation.

## GPU Experiment Commands

After copying `.env.example` to `.env`, setting provider/hourly metadata, and
confirming an NVIDIA runtime is available:

```bash
bash deploy/experiments/phase8_gpu_experiments.sh measure_context
bash deploy/experiments/phase8_gpu_experiments.sh start_stack
bash deploy/experiments/phase8_gpu_experiments.sh warmup
bash deploy/experiments/phase8_gpu_experiments.sh canonical_sweep
bash deploy/experiments/phase8_gpu_experiments.sh prefix_cache_comparison
bash deploy/experiments/phase8_gpu_experiments.sh full_1000_final
```

Run a small `max_num_seqs` comparison near the useful concurrency point by
setting `VLLM_MAX_NUM_SEQS` in `.env`, recreating `vllm`, repeating one
`canonical_100` run, and collecting artifacts. Only tune
`VLLM_MAX_NUM_BATCHED_TOKENS` if the baseline resource telemetry shows that it
is constraining throughput or queue behavior.

## Post-Run Collection

```bash
python3 -m portfolio.portfolio.deployment.collector \
  --benchmark-run-dir results/phase8/raw/<run_id> \
  --cost-profile configs/cost/reference_local.yaml \
  --output-dir results/phase8/analytics/<run_id> \
  --inference-profile configs/inference/cloud-gpu-baseline.yaml \
  --compose-file deploy/compose/compose.common.yaml \
  --compose-file deploy/compose/compose.gpu.yaml \
  ${POSTGRES_DSN:+--postgres-dsn "$POSTGRES_DSN"}
```

Optional `--jaeger-trace-json` and `--prometheus-samples-json` files can enrich
execution, inference, and resource observations. Span-derived inference records
use `otel:<trace_id>:<span_id>` as stable raw `observation_id`; Prometheus
histograms remain aggregate run telemetry unless exact request fields are
present in spans.

To fetch those telemetry JSON files from the local stack first:

```bash
python3 -m portfolio.portfolio.deployment.telemetry \
  --run-json results/phase8/raw/<run_id>/run.json \
  --output-dir results/phase8/telemetry \
  --include-gpu \
  --include-vllm
```

The optional vLLM queries use exact metric family names expected for the pinned
v0.8.5 OpenAI-compatible image. Before canonical GPU runs, inspect
`http://localhost:9090/targets` and `http://localhost:8000/metrics` on the GPU
host and update the optional query list only if the selected image exposes a
different metric family name.
