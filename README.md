# Portfolio Systems Take-Home

This repository runs the supplied multi-agent portfolio workflow behind a
Gateway, serves the Advisor through vLLM/OpenAI-compatible inference, collects
live telemetry, and turns completed benchmark runs into historical analytics,
cost attribution, Parquet artifacts, and a portable HTML report.

Start with:

- [System contracts](docs/architecture/system_contracts.md)
- [Architecture overview](docs/architecture/overview.md)
- [CPU demo walkthrough](docs/demo_walkthrough.md)
- [Experiment guide](docs/experiment_guide.md)
- [Cost methodology](docs/cost_methodology.md)
- [Data location matrix](docs/data_locations.md)

The CPU path is for noncanonical rehearsal and demo validation. Canonical
assignment measurements are intended for the single-GPU vLLM path using
`Qwen/Qwen3-4B-Instruct-2507`.
