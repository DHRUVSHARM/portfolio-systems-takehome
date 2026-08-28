#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose --env-file "$ROOT/deploy/compose/.env" -f "$ROOT/deploy/compose/compose.common.yaml" -f "$ROOT/deploy/compose/compose.gpu.yaml")
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/phase8}"
COST_PROFILE="${COST_PROFILE:-$ROOT/configs/cost/reference_local.yaml}"
INFERENCE_PROFILE="${INFERENCE_PROFILE:-$ROOT/configs/inference/cloud-gpu-baseline.yaml}"
RUN_PREFIX="${RUN_PREFIX:-phase8-gpu}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://localhost:8000}"

mkdir -p "$RESULTS_DIR"

measure_context() {
  python3 -m portfolio.portfolio.deployment.context_lengths \
    --dataset-mode canonical_100 \
    --model "${VLLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}" \
    --generation-allowance 256 \
    --output "$RESULTS_DIR/context_lengths_canonical_100.json"
}

start_stack() {
  "${COMPOSE[@]}" pull
  "${COMPOSE[@]}" up -d postgres jaeger loki alertmanager otel-collector prometheus grafana alloy node-exporter cadvisor dcgm-exporter vllm portfolio-api gateway
  "${COMPOSE[@]}" ps
}

warmup() {
  local run_id="$RUN_PREFIX-warmup-$(date -u +%Y%m%dT%H%M%SZ)"
  python3 -m portfolio.portfolio.benchmark \
    --gateway-base-url "$GATEWAY_BASE_URL" \
    --dataset-mode sampled_10 \
    --sample-seed 17 \
    --concurrency 2 \
    --request-timeout-seconds 180 \
    --run-id "$run_id" \
    --run-name "$run_id" \
    --output-root "$RESULTS_DIR/warmup"
}

run_benchmark() {
  local dataset="$1"
  local concurrency="$2"
  local label="$3"
  local run_id="$RUN_PREFIX-$label-c${concurrency}-$(date -u +%Y%m%dT%H%M%SZ)"
  python3 -m portfolio.portfolio.benchmark \
    --gateway-base-url "$GATEWAY_BASE_URL" \
    --dataset-mode "$dataset" \
    --concurrency "$concurrency" \
    --request-timeout-seconds 180 \
    --run-id "$run_id" \
    --run-name "$run_id" \
    --output-root "$RESULTS_DIR/raw"
  collect_run "$RESULTS_DIR/raw/$run_id" "$RESULTS_DIR/analytics/$run_id"
}

collect_run() {
  local run_dir="$1"
  local output_dir="$2"
  python3 -m portfolio.portfolio.deployment.collector \
    --benchmark-run-dir "$run_dir" \
    --cost-profile "$COST_PROFILE" \
    --output-dir "$output_dir" \
    --inference-profile "$INFERENCE_PROFILE" \
    --compose-file "$ROOT/deploy/compose/compose.common.yaml" \
    --compose-file "$ROOT/deploy/compose/compose.gpu.yaml" \
    ${POSTGRES_DSN:+--postgres-dsn "$POSTGRES_DSN"}
}

canonical_sweep() {
  for c in 1 2 4 8 16 32 64; do
    run_benchmark canonical_100 "$c" canonical100
  done
}

prefix_cache_comparison() {
  VLLM_ENABLE_PREFIX_CACHING= "${COMPOSE[@]}" up -d vllm
  run_benchmark canonical_100 "${PREFIX_CACHE_CONCURRENCY:-16}" prefix-off
  VLLM_ENABLE_PREFIX_CACHING=1 "${COMPOSE[@]}" up -d --force-recreate vllm
  run_benchmark canonical_100 "${PREFIX_CACHE_CONCURRENCY:-16}" prefix-on
}

full_1000_final() {
  run_benchmark full_1000 "${FULL_1000_CONCURRENCY:-16}" full1000
}

"$@"
