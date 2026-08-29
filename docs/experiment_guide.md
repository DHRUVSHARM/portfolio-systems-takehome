# Experiment Guide

## CPU Rehearsal

Use CPU rehearsal to verify stack wiring, telemetry, trace correlation, cost
pipeline, historical persistence, and report generation.

```bash
deploy/experiments/phase9_observability_demo.sh up
deploy/experiments/phase9_observability_demo.sh health
deploy/experiments/phase9_observability_demo.sh urls
deploy/experiments/phase9_observability_demo.sh single_request
deploy/experiments/phase9_observability_demo.sh small_benchmark
deploy/experiments/phase9_observability_demo.sh run_100 2
```

The CPU path is noncanonical and should not be used for final GPU performance
claims. The verified 100-query submission evidence is the concurrency-2 run
documented in [experiment_results.md](experiment_results.md).

CPU inference is substantially slower than the target GPU path. A request also
crosses several independently bounded timeout layers: the benchmark client,
Gateway downstream request, and Portfolio inference request. Higher-load
capacity experiments should therefore be run on the canonical GPU host with
those timeout and admission budgets calibrated together.

## Canonical GPU

Use `deploy/experiments/phase8_gpu_experiments.sh` on the GPU host after setting
a real provider cost profile and immutable model revision. Do not use local CPU
latency as a proxy for canonical GPU serving performance.

## Artifact Locations

```text
results/phase8/raw/<RUN_ID>/
results/phase8/telemetry/<RUN_ID>/
results/phase8/analytics/<RUN_ID>/
results/phase8/analytics/<RUN_ID>/report/index.html
```

See also:

- [experiment_results.md](experiment_results.md)
- [metrics_reference.md](metrics_reference.md)
- [tradeoffs_and_future_work.md](tradeoffs_and_future_work.md)
