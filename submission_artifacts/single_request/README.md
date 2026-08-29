# Single Request Validation

This experiment was used as a live end-to-end validation of the complete request path.

## Identity

- Run ID: `NONCANONICAL_CPU_INTEGRATION_SINGLE`
- Request ID: `cpu-rehearsal-single`
- Query ID: `cpu-rehearsal-single`
- Model: `Qwen/Qwen3-0.6B`
- Environment: noncanonical local CPU rehearsal

## Purpose

The request verified:

- Gateway -> Portfolio API routing
- Portfolio workflow execution
- MetricsAgent and PriceAgent execution
- RiskAgent execution
- AdvisorAgent inference through vLLM
- OpenTelemetry / Jaeger trace propagation
- run_id / request_id / query_id correlation
- inference token metadata
- actual model-generated Advisor output

This single-request check was performed as a live observability validation rather than as a frozen benchmark artifact bundle.

The persisted benchmark evidence is available in:

- `../sampled_10/`
- `../canonical_100_c2/`

See also:

- `../../docs/experiment_results.md`
- `../../docs/demo1_diagrams.md`
