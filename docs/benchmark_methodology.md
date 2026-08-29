# Benchmark Methodology

The benchmark uses deterministic query normalization and closed-loop async load
generation with one reused HTTP client. It preserves one LLM Advisor call per
successful portfolio request.

Required dataset modes:

- `canonical_100`
- `full_1000`
- `sampled_N`

Canonical GPU runs should follow:

```text
prepare model cache
start stack
wait for vLLM model readiness
wait for Gateway and Portfolio readiness
warm up
run measured benchmark
export Jaeger and Prometheus telemetry
collect analytics
open historical Grafana and report/index.html
```

CPU rehearsal uses the same application contracts but may use a smaller
noncanonical model for local validation.
