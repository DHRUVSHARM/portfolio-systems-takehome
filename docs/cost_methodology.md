# Cost Methodology

Total infrastructure cost is:

```text
measured run duration hours * machine hourly USD
```

The machine hourly rate comes from the selected versioned cost profile. For a
bundled GPU VM, do not separately add CPU, RAM, and GPU line items if the
provider price already includes them.

Request attribution allocates the shared run cost downstream:

- CPU pool: measured CPU seconds when available, otherwise documented proxy.
- GPU pool: shared token work, not request wall time, to avoid double-counting
  overlapping continuous batches.
- Overhead pool: explicit `overhead_unallocated`, never silently discarded.

Assignment metrics are available in:

- HTML report -> `Assignment Metrics`
- `metrics.json`
- `report.json`
- PostgreSQL `derived_metrics`
- PostgreSQL `request_cost_attributions`
- PostgreSQL `agent_cost_attributions`

CPU runs using `reference_cpu_demo` are noncanonical and illustrative only.
