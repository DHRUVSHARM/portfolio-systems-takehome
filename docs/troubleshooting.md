# Troubleshooting

## vLLM is not ready

Check the `vllm` container logs and `/v1/models`. CPU rehearsal may need a
smaller model and the `PORTFOLIO_INFERENCE_ENABLE_THINKING=false` override for
Qwen3 CPU compatibility.

## Grafana has no historical rows

Confirm the collector ran with a PostgreSQL DSN and that the selected `run_id`
and `profile_id` variables have values. The generated HTML report can still be
opened from disk if PostgreSQL is unavailable.

## Jaeger has no trace

Verify tracing is enabled and sampled. Historical reproducibility does not rely
on live Jaeger retention once `*_jaeger_traces.json` has been exported and
collected.

## GPU panels are empty on CPU

That is expected. CPU rehearsal should show `N/A / telemetry unavailable for
this run` for GPU-only fields.

## Cost is zero

`reference_local` intentionally has zero hourly cost. CPU demo runs that need
the cost/report pipeline should use `reference_cpu_demo`. Canonical GPU runs
must use a real provider cost profile.
