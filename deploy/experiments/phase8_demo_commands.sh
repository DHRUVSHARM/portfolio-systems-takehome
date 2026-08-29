#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://localhost:8000}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/phase8-demo}"

single_request() {
  python3 -c 'import json, urllib.request
payload={"holdings":{"AAPL":0.4,"MSFT":0.35,"NVDA":0.25},"lookback_days":180}
req=urllib.request.Request("'"$GATEWAY_BASE_URL"'/v1/analyze", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json","X-Run-ID":"demo-single","X-Request-ID":"demo-single-1","X-Query-ID":"demo-single"}, method="POST")
print(urllib.request.urlopen(req, timeout=180).read().decode())'
}

demo_100() {
  python3 -m portfolio.portfolio.benchmark \
    --gateway-base-url "$GATEWAY_BASE_URL" \
    --dataset-mode canonical_100 \
    --concurrency "${DEMO_100_CONCURRENCY:?set DEMO_100_CONCURRENCY from tuned canonical results}" \
    --request-timeout-seconds 180 \
    --run-name demo-canonical-100 \
    --output-root "$RESULTS_DIR/raw"
}

demo_1000() {
  python3 -m portfolio.portfolio.benchmark \
    --gateway-base-url "$GATEWAY_BASE_URL" \
    --dataset-mode full_1000 \
    --concurrency "${DEMO_1000_CONCURRENCY:?set DEMO_1000_CONCURRENCY from tuned canonical results}" \
    --request-timeout-seconds 180 \
    --run-name demo-full-1000 \
    --output-root "$RESULTS_DIR/raw"
}

show_results() {
  local run_id="${1:?run_id required}"
  python3 -m json.tool "$RESULTS_DIR/analytics/$run_id/report.json"
}

"$@"
