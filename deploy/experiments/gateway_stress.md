# Gateway-Only Stress Mode

Gateway stress is intentionally separate from canonical inference experiments.
It should target a cheap fake Portfolio downstream and must not invoke Qwen or
vLLM. Use it only to validate Gateway admission behavior:

- active downstream calls never exceed `GATEWAY_MAX_IN_FLIGHT`
- waiting requests never exceed `GATEWAY_QUEUE_CAPACITY`
- excess requests receive controlled `503` responses
- queue timeout returns `503`
- cancellation releases admission safely
- the system recovers after saturation

Suggested local command after starting a stub Portfolio service:

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

Do not use results from this mode as Advisor/vLLM cost or latency evidence.
