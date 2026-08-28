#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHASE8_CPU="$ROOT/deploy/experiments/phase8_cpu_rehearsal.sh"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/compose/.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT/deploy/compose/compose.common.yaml" -f "$ROOT/deploy/compose/compose.cpu.yaml")
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/phase8}"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://localhost:8000}"
JAEGER_BASE_URL="${JAEGER_BASE_URL:-http://localhost:16686}"
PROMETHEUS_BASE_URL="${PROMETHEUS_BASE_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"

compose() {
  "${COMPOSE[@]}" "$@"
}

latest_run_id() {
  find "$RESULTS_DIR/analytics" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {print $2}'
}

print_next_commands() {
  local run_id="$1"
  cat <<COMMANDS
RUN_ID=$run_id
Next:
  deploy/experiments/phase9_observability_demo.sh show_paths $run_id
  deploy/experiments/phase9_observability_demo.sh render_report $run_id
  deploy/experiments/phase9_observability_demo.sh verify_run $run_id
  HTML report: results/phase8/analytics/$run_id/report/index.html
COMMANDS
}

up() {
  "$PHASE8_CPU" start_stack
}

health() {
  python3 -c 'import json, urllib.request, os
urls={
  "gateway": os.environ.get("GATEWAY_BASE_URL", "http://localhost:8000") + "/ready",
  "prometheus": os.environ.get("PROMETHEUS_BASE_URL", "http://localhost:9090") + "/-/ready",
  "jaeger": os.environ.get("JAEGER_BASE_URL", "http://localhost:16686") + "/",
}
status={}
for name, url in urls.items():
    try:
        urllib.request.urlopen(url, timeout=5).read()
        status[name]="ok"
    except Exception as exc:
        status[name]=type(exc).__name__
print(json.dumps(status, sort_keys=True))'
}

urls() {
  cat <<URLS
LIVE
Gateway:    $GATEWAY_BASE_URL
Grafana:    $GRAFANA_URL
Jaeger:     $JAEGER_BASE_URL
Prometheus: $PROMETHEUS_BASE_URL
Loki:       open Grafana -> Explore -> Loki datasource

GRAFANA DASHBOARDS
System Overview
Gateway
Agent / Workflow Breakdown
vLLM Inference
Resources
Live Benchmark
Portfolio Historical Analytics
URLS
}

single_request() {
  "$PHASE8_CPU" single_request
}

small_benchmark() {
  "$PHASE8_CPU" small_benchmark
  local run_id
  run_id="$(latest_run_id)"
  if [[ -n "$run_id" ]]; then
    print_next_commands "$run_id"
  fi
}

run_100() {
  local concurrency="${1:-2}"

  echo "CPU 100-query experiment"
  echo "  dataset: canonical_100"
  echo "  concurrency: $concurrency"
  echo "  mode: NONCANONICAL CPU"
  echo

  "$PHASE8_CPU" canonical_100 "$concurrency"

  local run_id
  run_id="$(latest_run_id)"

  if [[ -n "$run_id" ]]; then
    echo
    echo "NEXT / OPEN"
    echo "  Grafana: $GRAFANA_URL"
    echo "  Jaeger:  $JAEGER_BASE_URL"
    echo "  Jaeger tag: {\"run_id\":\"$run_id\"}"
    echo "  HTML report: $RESULTS_DIR/analytics/$run_id/report/index.html"
  fi
}

collect_run() {
  local run_id="${1:?run_id required}"
  "$PHASE8_CPU" export_telemetry "$run_id"
  "$PHASE8_CPU" collect_run "$run_id"
  print_next_commands "$run_id"
}

render_report() {
  local run_id="${1:?run_id required}"
  compose run --rm deployment-tools \
    python -m portfolio.portfolio.analytics.exporters.visual_report \
      --run-dir "/results/phase8/analytics/$run_id"
}

verify_run() {
  local run_id="${1:?run_id required}"
  "$PHASE8_CPU" verify_artifacts "$run_id"
  python3 -c 'import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
required=[
  root/"report"/"index.html",
  root/"serving_telemetry.json",
  root/"report"/"assets"/"cost_query_histogram.svg",
  root/"report"/"assets"/"agent_cost_share.svg",
]
missing=[str(path) for path in required if not path.exists() or path.stat().st_size == 0]
assert not missing, missing
html=(root/"report"/"index.html").read_text()
assert "Assignment Metrics" in html
assert "Request Drilldown" in html
print(json.dumps({"status":"ok","html_report":str(root/"report"/"index.html")}, sort_keys=True))' "$RESULTS_DIR/analytics/$run_id"
}

show_paths() {
  local run_id="${1:?run_id required}"
  cat <<PATHS
HISTORICAL
Historical Grafana: open $GRAFANA_URL -> Dashboards -> Portfolio Historical Analytics
Analytics directory: $RESULTS_DIR/analytics/$run_id
HTML report: $RESULTS_DIR/analytics/$run_id/report/index.html
Raw benchmark: $RESULTS_DIR/raw/$run_id
Frozen telemetry: $RESULTS_DIR/telemetry/$run_id
PostgreSQL: compose service postgres, database portfolio_analytics
Parquet: $RESULTS_DIR/analytics/$run_id/*.parquet
JSON metrics: $RESULTS_DIR/analytics/$run_id/metrics.json
Run report JSON: $RESULTS_DIR/analytics/$run_id/report.json
PATHS
}

demo() {
  urls
  up
  health
  single_request
  small_benchmark
}

"$@"
