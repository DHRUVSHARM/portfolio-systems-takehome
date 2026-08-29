# Codex Execution Workflow

The detailed phase plans live in the repository so Codex prompts can remain extremely short. Do not paste the full phase specification into Codex again.

## Before starting

Pull the latest `systems-implementation` branch locally.

For every phase, Codex must read:

1. `docs/architecture/system_contracts.md`
2. the current phase document under `docs/phases/`
3. the existing implementation relevant to that phase

Treat the repo documents as authoritative for scope, interfaces, tests and acceptance criteria.

## Phase 2 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_02_inference.md.
Inspect the existing Phase 1 implementation first.
Implement only Phase 2 exactly to the documented acceptance criteria.
Run only the focused Phase 2 tests unless a compatibility failure requires broader testing.
Do not implement future phases.
```

## Phase 3 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_03_portfolio_api.md.
Assume completed earlier phases are authoritative and inspect them before editing.
Implement only Phase 3 and run only its focused tests.
Do not redesign Phase 1/2 or implement future phases.
```

## Phase 4 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_04_gateway.md.
Inspect the current Portfolio API and runtime before editing.
Implement only Phase 4 and run its focused tests, then the single full-suite Checkpoint 1 validation required by the phase document.
Do not implement Phase 5+.
```

After Phase 4, stop implementation long enough to perform Checkpoint 1 Git/code review of the complete request path.

## Phase 5 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_05_benchmark.md.
Inspect the existing deterministic benchmark adapter and public Gateway contract first.
Implement only Phase 5 and run only its focused tests.
Do not implement observability, analytics or deployment work.
```

## Phase 6 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_06_observability.md.
Inspect the existing Gateway, Portfolio runtime, benchmark identities and inference path first.
Implement only Phase 6 and run its focused tests, then the single full-suite Checkpoint 2 validation required by the phase document.
Do not implement Phase 7/8 analytics or final experiments.
```

After Phase 6, perform Checkpoint 2 Git/code review focused on measurement correctness before cost analytics.

## Phase 7 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_07_analytics_cost.md.
Inspect existing raw benchmark/telemetry observation shapes first.
Implement only Phase 7 and run only its focused tests.
Do not provision cloud infrastructure or run final GPU experiments.
```

## Phase 8 prompt

```text
Read docs/architecture/system_contracts.md and docs/phases/phase_08_deployment_experiments.md.
Inspect all completed services/config/analytics before integrating them.
Implement only Phase 8 according to the documented deployment and experiment plan.
Avoid unnecessary repeated full-suite runs; perform the comprehensive final validation once the stack is ready.
```

## Review cadence

Only three broad reviews are intended:

1. Checkpoint 1 after Phase 4: request-path correctness.
2. Checkpoint 2 after Phase 6: benchmark and observability correctness.
3. Final review after Phase 8: complete deployment, analytics, experiments and assignment deliverables.

Focused phase tests should catch local regressions between checkpoints.

## Git responsibility

Phase specs intentionally omit `git add`, `git commit`, `git push`, branch manipulation and repetitive `git diff` instructions. The developer owns Git workflow separately to conserve Codex context/tokens.

## Scope rule for Codex

When implementation details in the current code differ slightly from suggested file trees, preserve the architectural contract and adapt to the existing repository instead of performing large cosmetic restructures. If a phase exposes a real conflict with an earlier contract, stop and surface the conflict rather than silently redesigning completed work.