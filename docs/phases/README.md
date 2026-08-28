# Remaining Implementation Phases

This directory is the execution plan for the rest of the take-home. Read `docs/architecture/system_contracts.md` before every phase.

## Status

- [x] Workload restoration
- [x] Phase 1: serving-safe PortfolioRuntime
- [x] Phase 2: async vLLM Advisor
- [x] Phase 3: internal Portfolio API
- [x] Phase 4: public Gateway and admission control
- [x] Phase 5: benchmark/load generator
- [x] Phase 6: deep observability
- [x] Phase 7: historical analytics and cost accounting
- [x] Phase 8: Docker Compose deployment and experiment infrastructure
- [x] Phase 9: observability, analytics, visualization, and demo experience

## Review checkpoints

Checkpoint 1: after Phase 4. Review the complete request path: Client -> Gateway -> Portfolio API -> PortfolioRuntime -> agents -> vLLM interface.

Checkpoint 2: after Phase 6. Review benchmark generation, metrics, traces, logs, agent drill-down and measurement correctness before trusting historical analytics.

Final review: after Phase 8. Review deployment, reproducibility, experiment methodology, cost accounting, charts, README and assignment deliverables.

## Minimal Codex invocation

For each phase, tell Codex only:

```text
Read docs/architecture/system_contracts.md.
Read docs/phases/phase_0X_<name>.md.
Inspect the existing implementation first.
Implement only that phase and run only the focused tests required by the phase document.
Do not implement future phases.
```

Do not paste the full plan again. These repo documents are the authoritative prompt.

## Test policy

Focused tests after each phase. Full suite only at the explicit checkpoints and final validation unless a focused test exposes a compatibility issue that requires broader testing.

## Git policy

Git add/commit/push is intentionally not part of these phase prompts. The developer controls Git separately.
