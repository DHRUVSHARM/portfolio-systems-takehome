#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/compose/.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose/compose.common.yaml" -f "$ROOT/deploy/compose/compose.gpu.yaml")
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/phase8}"
RUN_PREFIX="${RUN_PREFIX:-phase8-gpu}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://localhost:8000}"
VLLM_INTERNAL_BASE_URL="${VLLM_INTERNAL_BASE_URL:-http://vllm:8000}"
JAEGER_BASE_URL="${JAEGER_BASE_URL:-http://localhost:16686}"
PROMETHEUS_BASE_URL="${PROMETHEUS_BASE_URL:-http://localhost:9090}"
POSTGRES_DSN_IN_COMPOSE="${POSTGRES_DSN_IN_COMPOSE:-postgresql://portfolio:replace-with-local-secret@postgres:5432/portfolio_analytics}"
BASELINE_INFERENCE_PROFILE="$ROOT/configs/inference/cloud-gpu-baseline.yaml"
PREFIX_INFERENCE_PROFILE="$ROOT/configs/inference/cloud-gpu-prefix-cache.yaml"
INFERENCE_PROFILE="${INFERENCE_PROFILE:-$BASELINE_INFERENCE_PROFILE}"
RUNTIME_COST_PROFILE_DIR="$RESULTS_DIR/runtime-cost-profiles"
CONTEXT_RESULT="${CONTEXT_RESULT:-$RESULTS_DIR/context/context_lengths_canonical_100.json}"
COST_PROFILE="${COST_PROFILE:-}"

mkdir -p "$RESULTS_DIR"

compose() {
  "${COMPOSE[@]}" "$@"
}

resolve_revision() {
  local output="$RESULTS_DIR/model-revision/qwen3_4b_instruct_2507.json"
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.deployment.model_revision \
      --model "${VLLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}" \
      --output "/results/phase8/model-revision/qwen3_4b_instruct_2507.json"
  export VLLM_MODEL_REVISION
  VLLM_MODEL_REVISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["resolved_revision"])' "$output")"
  echo "Resolved immutable model revision: $VLLM_MODEL_REVISION"
}

measure_context() {
  require_revision
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.deployment.context_lengths \
      --dataset-mode canonical_100 \
      --model "${VLLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}" \
      --revision "$VLLM_MODEL_REVISION" \
      --generation-allowance 256 \
      --output /results/phase8/context/context_lengths_canonical_100.json
}

create_gpu_cost_profile() {
  local created
  created="$(python3 -m portfolio.portfolio.deployment.cost_profiles create-cloud-gpu \
    --output-dir "$RUNTIME_COST_PROFILE_DIR")"
  export COST_PROFILE="$created"
  echo "Created canonical GPU cost profile: $COST_PROFILE"
}

start_stack() {
  compose pull
  compose up -d postgres jaeger loki alertmanager otel-collector prometheus grafana alloy node-exporter cadvisor dcgm-exporter vllm portfolio-api gateway
  wait_stack_ready
}

wait_stack_ready() {
  wait_container_url vllm "http://localhost:8000/v1/models" "vLLM model API"
  verify_served_model
  wait_container_url portfolio-api "http://localhost:8000/ready" "Portfolio"
  wait_url "$GATEWAY_BASE_URL/ready" "Gateway"
}

wait_url() {
  local url="$1"
  local label="$2"
  for _ in $(seq 1 120); do
    if python3 -c "import urllib.request; urllib.request.urlopen('$url', timeout=5).read()" >/dev/null 2>&1; then
      echo "$label ready"
      return 0
    fi
    sleep 2
  done
  echo "$label did not become ready: $url" >&2
  return 1
}

wait_container_url() {
  local service="$1"
  local url="$2"
  local label="$3"
  for _ in $(seq 1 120); do
    if compose exec -T "$service" python -c "import urllib.request; urllib.request.urlopen('$url', timeout=5).read()" >/dev/null 2>&1; then
      echo "$label ready"
      return 0
    fi
    sleep 2
  done
  echo "$label did not become ready inside $service: $url" >&2
  return 1
}

verify_served_model() {
  compose exec -T vllm python -c 'import json, os, urllib.request
url="http://localhost:8000/v1/models"
expected=os.environ.get("VLLM_SERVED_MODEL_NAME") or os.environ.get("VLLM_MODEL") or "Qwen/Qwen3-4B-Instruct-2507"
data=json.loads(urllib.request.urlopen(url, timeout=10).read())
served=[item.get("id") for item in data.get("data", [])]
assert expected in served, f"expected served model {expected!r}, got {served!r}"'
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
  local inference_profile="$4"
  local run_id="$RUN_PREFIX-$label-c${concurrency}-$(date -u +%Y%m%dT%H%M%SZ)"
  preflight_canonical "$inference_profile"
  python3 -m portfolio.portfolio.benchmark \
    --gateway-base-url "$GATEWAY_BASE_URL" \
    --dataset-mode "$dataset" \
    --concurrency "$concurrency" \
    --request-timeout-seconds 180 \
    --run-id "$run_id" \
    --run-name "$run_id" \
    --output-root "$RESULTS_DIR/raw"
  export_required_telemetry "$run_id"
  collect_run "$RESULTS_DIR/raw/$run_id" "$RESULTS_DIR/analytics/$run_id" "$inference_profile"
}

export_required_telemetry() {
  local run_id="$1"
  local telemetry_dir="$RESULTS_DIR/telemetry/$run_id"
  mkdir -p "$telemetry_dir"
  if ! python3 -m portfolio.portfolio.deployment.telemetry export-run \
      --run-json "$RESULTS_DIR/raw/$run_id/run.json" \
      --output-dir "$telemetry_dir" \
      --jaeger-base-url "$JAEGER_BASE_URL" \
      --prometheus-base-url "$PROMETHEUS_BASE_URL" \
      --include-gpu \
      --include-vllm; then
    python3 -c 'import json, pathlib, sys
path=pathlib.Path(sys.argv[1]) / "telemetry_incomplete.json"
path.write_text(json.dumps({"status":"incomplete","invalid_reason":"required telemetry export failed","raw_benchmark_artifacts_preserved":True}, indent=2) + "\n")' "$telemetry_dir"
    return 1
  fi
}

collect_run() {
  local run_dir="$1"
  local output_dir="$2"
  local inference_profile="$3"
  local run_id
  run_id="$(basename "$run_dir")"
  local container_cost_profile
  local container_inference_profile
  container_cost_profile="$(container_results_path "$COST_PROFILE")"
  container_inference_profile="$(container_repo_path "$inference_profile")"
  compose run --rm \
    -e POSTGRES_DSN="$POSTGRES_DSN_IN_COMPOSE" \
    deployment-tools \
    python -m portfolio.portfolio.deployment.collector \
    --benchmark-run-dir "/results/phase8/raw/$run_id" \
    --cost-profile "$container_cost_profile" \
    --output-dir "/results/phase8/analytics/$run_id" \
    --inference-profile "$container_inference_profile" \
    --jaeger-trace-json "/results/phase8/telemetry/$run_id/${run_id}_jaeger_traces.json" \
    --prometheus-samples-json "/results/phase8/telemetry/$run_id/${run_id}_prometheus_samples.json" \
    --require-telemetry \
    --postgres-dsn "$POSTGRES_DSN_IN_COMPOSE" \
    --compose-file /app/deploy/compose/compose.common.yaml \
    --compose-file /app/deploy/compose/compose.gpu.yaml
}

container_results_path() {
  local path="$1"
  if [[ "$path" == "$RESULTS_DIR/"* ]]; then
    echo "/results/phase8/${path#"$RESULTS_DIR/"}"
  else
    container_repo_path "$path"
  fi
}

container_repo_path() {
  local path="$1"
  if [[ "$path" == "$ROOT/"* ]]; then
    echo "/app/${path#"$ROOT/"}"
  else
    echo "$path"
  fi
}

preflight_canonical() {
  local inference_profile="$1"
  require_nvidia
  require_revision
  require_context_measured
  require_cost_profile
  require_profile_matches_runtime "$inference_profile"
  wait_container_url vllm "http://localhost:8000/v1/models" "vLLM model API"
  verify_served_model
  wait_container_url portfolio-api "http://localhost:8000/ready" "Portfolio"
  wait_url "$GATEWAY_BASE_URL/ready" "Gateway"
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.deployment.telemetry vllm-preflight \
      --vllm-base-url "$VLLM_INTERNAL_BASE_URL" \
      --output "/results/phase8/telemetry/vllm_preflight_$(date -u +%Y%m%dT%H%M%SZ).json" >/dev/null
  python3 -c "import urllib.request; urllib.request.urlopen('$JAEGER_BASE_URL/api/services', timeout=10).read()" >/dev/null
  python3 -c "import urllib.request; urllib.request.urlopen('$PROMETHEUS_BASE_URL/api/v1/targets', timeout=10).read()" >/dev/null
}

require_nvidia() {
  command -v nvidia-smi >/dev/null
  nvidia-smi >/dev/null
}

require_revision() {
  python3 -c 'import os,re
rev=os.environ.get("VLLM_MODEL_REVISION","")
assert re.fullmatch(r"[0-9a-f]{40}", rev), "VLLM_MODEL_REVISION must be an immutable 40-character Hugging Face commit SHA"'
}

require_context_measured() {
  python3 -c 'import json, os, sys
path=os.environ.get("CONTEXT_RESULT", sys.argv[1])
data=json.load(open(path))
status=data.get("status")
assert status == "measured", f"context measurement must be measured, got {status!r}"
configured=int(os.environ.get("VLLM_MAX_MODEL_LEN", "0"))
recommended=int(data["recommended_max_model_len"])
assert configured >= recommended, f"VLLM_MAX_MODEL_LEN={configured} < recommended {recommended}"' "$CONTEXT_RESULT"
}

require_cost_profile() {
  if [[ -z "${COST_PROFILE:-}" ]]; then
    create_gpu_cost_profile
  fi
  python3 -m portfolio.portfolio.deployment.cost_profiles validate-canonical --profile "$COST_PROFILE" >/dev/null
}

require_profile_matches_runtime() {
  local inference_profile="$1"
  local prefix_expected="false"
  if [[ "$inference_profile" == "$PREFIX_INFERENCE_PROFILE" ]]; then
    prefix_expected="true"
  fi
  python3 -c 'import os, sys
profile=sys.argv[1]
expected=sys.argv[2] == "true"
runtime=bool(os.environ.get("VLLM_ENABLE_PREFIX_CACHING"))
assert runtime == expected, f"prefix-cache runtime={runtime} does not match profile {profile}"' "$inference_profile" "$prefix_expected"
}

canonical_sweep() {
  for c in 1 2 4 8 16 32 64; do
    run_benchmark canonical_100 "$c" canonical100 "$BASELINE_INFERENCE_PROFILE"
  done
}

prefix_cache_comparison() {
  local c="${PREFIX_CACHE_CONCURRENCY:?PREFIX_CACHE_CONCURRENCY must be selected from canonical_100 tuning results}"
  VLLM_ENABLE_PREFIX_CACHING= compose up -d --force-recreate vllm
  wait_stack_ready
  VLLM_ENABLE_PREFIX_CACHING= warmup
  VLLM_ENABLE_PREFIX_CACHING= run_benchmark canonical_100 "$c" prefix-off "$BASELINE_INFERENCE_PROFILE"

  VLLM_ENABLE_PREFIX_CACHING=1 compose up -d --force-recreate vllm
  wait_stack_ready
  VLLM_ENABLE_PREFIX_CACHING=1 warmup
  VLLM_ENABLE_PREFIX_CACHING=1 run_benchmark canonical_100 "$c" prefix-on "$PREFIX_INFERENCE_PROFILE"
}

full_1000_final() {
  local c="${FULL_1000_CONCURRENCY:?FULL_1000_CONCURRENCY must be explicitly selected from tuning results}"
  run_benchmark full_1000 "$c" full1000 "$INFERENCE_PROFILE"
}

"$@"
