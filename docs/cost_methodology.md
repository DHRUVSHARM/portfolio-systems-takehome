# Cost Methodology

Total infrastructure cost is:

```text
measured run duration hours * machine hourly USD
```

The machine hourly rate comes from the selected versioned cost profile. For a
bundled GPU VM, do not separately add CPU, RAM, and GPU line items if the
provider price already includes them.

The local CPU demo profile is
[configs/cost/reference_cpu_demo.yaml](../configs/cost/reference_cpu_demo.yaml):

- machine hourly USD: `1.00`
- CPU pool: `35%`
- inference/GPU pool: `45%`
- overhead pool: `20%`
- prefill token weight: `1`
- decode token weight: `4`

Those fractions are configurable accounting assumptions for the CPU demo. They
are not measured physical hardware fractions.

Request attribution allocates the shared run cost downstream:

- CPU pool: measured CPU seconds when available, otherwise exclusive nested-span
  wall time as a documented proxy. Child span work is subtracted from parent
  work to avoid double counting.
- Inference/GPU pool: shared token work, not request wall time, to avoid
  double-counting overlapping continuous batches.
- Overhead pool: explicit `overhead_unallocated`, never silently discarded.

Token work is:

```text
prompt_tokens * prefill_token_weight + completion_tokens * decode_token_weight
```

Prompt tokens primarily represent prefill/input processing. Completion tokens
represent autoregressive decode/output generation. The demo `4x` decode weight
is a configurable heuristic, not a universal constant.

At agent level, AdvisorAgent receives the shared inference pool. At request
level, the inference pool is divided by weighted token work. Request-level
overhead is allocated by request wall time; agent-level overhead remains
`overhead_unallocated`.

The collector can use trace-level request identity as a fallback for child spans
that do not repeat every request tag, and it can recover `query_id` from the
benchmark request mapping. This keeps CPU-side agent attribution visible.

Assignment metrics are available in:

- HTML report -> `Assignment Metrics`
- `metrics.json`
- `report.json`
- PostgreSQL `derived_metrics`
- PostgreSQL `request_cost_attributions`
- PostgreSQL `agent_cost_attributions`

CPU runs using `reference_cpu_demo` are noncanonical and illustrative only.
