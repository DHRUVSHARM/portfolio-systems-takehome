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
claims.

`run_100 16` is available for a deliberate local CPU concurrency rehearsal, but
the verified successful submission evidence is the C2 100-query run documented
in [experiment_results.md](experiment_results.md).

## Canonical GPU

Use `deploy/experiments/phase8_gpu_experiments.sh` on the GPU host after setting
a real provider cost profile and immutable model revision. Do not run GPU
experiments from Phase 9 locally.

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
