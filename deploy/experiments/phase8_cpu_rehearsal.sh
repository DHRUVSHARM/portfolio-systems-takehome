#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/compose/.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose/compose.common.yaml" -f "$ROOT/deploy/compose/compose.cpu.yaml")
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/phase8}"
RUN_LABEL="NONCANONICAL_CPU_INTEGRATION"
ENABLE_HOST_EXPORTERS="${ENABLE_HOST_EXPORTERS:-false}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://localhost:8000}"
JAEGER_BASE_URL="${JAEGER_BASE_URL:-http://localhost:16686}"
PROMETHEUS_BASE_URL="${PROMETHEUS_BASE_URL:-http://localhost:9090}"
COST_PROFILE="$ROOT/configs/cost/reference_cpu_demo.yaml"
INFERENCE_PROFILE="$ROOT/configs/inference/local-cpu.yaml"
POSTGRES_DSN_IN_COMPOSE="${POSTGRES_DSN_IN_COMPOSE:-postgresql://portfolio:replace-with-local-secret@postgres:5432/portfolio_analytics}"

ENV_VLLM_MODEL="$(grep -m1 '^VLLM_MODEL=' "$ENV_FILE" | cut -d= -f2- || true)"
CPU_MODEL="${VLLM_MODEL:-${ENV_VLLM_MODEL:-Qwen/Qwen3-4B-Instruct-2507}}"

mkdir -p "$RESULTS_DIR"

compose() {
  "${COMPOSE[@]}" "$@"
}

start_stack() {
  compose pull --ignore-pull-failures

  local services=(
    postgres
    jaeger
    loki
    alertmanager
    otel-collector
    prometheus
    grafana
    alloy
    vllm
    portfolio-api
    gateway
  )

  if [[ "$ENABLE_HOST_EXPORTERS" == "true" ]]; then
    services+=(node-exporter cadvisor)
  else
    echo "Skipping node-exporter/cadvisor for local CPU rehearsal"
  fi

  compose up -d "${services[@]}"
  wait_stack_ready
}

measure_context() {
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.deployment.context_lengths \
      --dataset-mode sampled_10 \
      --model "$CPU_MODEL" \
      --generation-allowance 256 \
      --output /results/phase8/context/context_lengths_cpu_rehearsal.json
}

wait_stack_ready() {
  wait_container_url vllm "http://localhost:8000/v1/models" "CPU vLLM model API"
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

single_request() {
  python3 -c 'import json, urllib.request
payload={"holdings":{"AAPL":0.4,"MSFT":0.35,"NVDA":0.25},"lookback_days":90}
req=urllib.request.Request("http://localhost:8000/v1/analyze", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json","X-Run-ID":"NONCANONICAL_CPU_INTEGRATION_SINGLE","X-Request-ID":"cpu-rehearsal-single","X-Query-ID":"cpu-rehearsal-single"}, method="POST")
data=json.loads(urllib.request.urlopen(req, timeout=180).read())
for key in ("metrics","risk","summary"):
    assert key in data, f"business response missing {key}"
print(json.dumps({"status":"ok","fields":sorted(data.keys())}, sort_keys=True))'
}

small_benchmark() {
  local run_id="$RUN_LABEL-$(date -u +%Y%m%dT%H%M%SZ)"
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.benchmark \
      --gateway-base-url "http://gateway:8000" \
      --dataset-mode sampled_10 \
      --sample-seed 81 \
      --concurrency 2 \
      --request-timeout-seconds 180 \
      --run-id "$run_id" \
      --run-name "$RUN_LABEL" \
      --output-root "/results/phase8/raw"
  export_telemetry "$run_id"
  collect_run "$run_id"
  verify_artifacts "$run_id"
}

canonical_100() {
  local concurrency="${1:-2}"
  local timeout_seconds="${CANONICAL_100_TIMEOUT_SECONDS:-180}"
  local run_id="NONCANONICAL_CPU_CANONICAL_100_C${concurrency}-$(date -u +%Y%m%dT%H%M%SZ)"

  echo "[1/4] Running canonical_100"
  echo "      concurrency=$concurrency"
  echo "      request_timeout_seconds=$timeout_seconds"
  echo "      run_id=$run_id"

  compose run --rm deployment-tools \
    python -m portfolio.portfolio.benchmark \
      --gateway-base-url "http://gateway:8000" \
      --dataset-mode canonical_100 \
      --concurrency "$concurrency" \
      --request-timeout-seconds "$timeout_seconds" \
      --run-id "$run_id" \
      --run-name "NONCANONICAL_CPU_CANONICAL_100_C${concurrency}" \
      --output-root "/results/phase8/raw"

  echo "[2/4] Exporting telemetry"
  export_telemetry "$run_id"

  echo "[3/4] Building analytics and HTML report"
  collect_run "$run_id"

  echo "[4/4] Verifying artifacts"
  verify_artifacts "$run_id"

  echo
  echo "RUN_ID=$run_id"
  echo "REPORT=$RESULTS_DIR/analytics/$run_id/report/index.html"
  echo "JAEGER_TAG={\"run_id\":\"$run_id\"}"
  echo "GRAFANA=http://localhost:3000"
  echo "JAEGER=http://localhost:16686"
}

export_telemetry() {
  local run_id="$1"
  local telemetry_dir="$RESULTS_DIR/telemetry/$run_id"
  mkdir -p "$telemetry_dir"
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.deployment.telemetry export-run \
      --run-json "/results/phase8/raw/$run_id/run.json" \
      --output-dir "/results/phase8/telemetry/$run_id" \
      --jaeger-base-url "http://jaeger:16686" \
      --prometheus-base-url "http://prometheus:9090" \
      --include-vllm
}

collect_run() {
  local run_id="$1"
  compose run --rm \
    -e POSTGRES_DSN="$POSTGRES_DSN_IN_COMPOSE" \
    deployment-tools \
    python -m portfolio.portfolio.deployment.collector \
      --benchmark-run-dir "/results/phase8/raw/$run_id" \
      --cost-profile /app/configs/cost/reference_cpu_demo.yaml \
      --output-dir "/results/phase8/analytics/$run_id" \
      --inference-profile /app/configs/inference/local-cpu.yaml \
      --jaeger-trace-json "/results/phase8/telemetry/$run_id/${run_id}_jaeger_traces.json" \
      --prometheus-samples-json "/results/phase8/telemetry/$run_id/${run_id}_prometheus_samples.json" \
      --require-telemetry \
      --postgres-dsn "$POSTGRES_DSN_IN_COMPOSE" \
      --compose-file /app/deploy/compose/compose.common.yaml \
      --compose-file /app/deploy/compose/compose.cpu.yaml
}

verify_artifacts() {
  local run_id="$1"
  python3 -c 'import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
report=json.loads((root / "report.json").read_text())
assert report["total_run_cost_usd"] > 0
assert report["request_cost_sum_usd"] > 0
assert "cost_per_query_distribution_usd" in report["assignment_metrics"]
assert (root / "charts" / "query_type_breakdown.json").exists()
assert (root / "charts" / "holdings_count_breakdown.json").exists()
print(json.dumps({"status":"ok","run_id":report["run_id"],"cost_profile":report["cost_profile"]}, sort_keys=True))' "$RESULTS_DIR/analytics/$run_id"
}

rehearsal() {
  start_stack
  measure_context
  single_request
  small_benchmark
}

"$@"
