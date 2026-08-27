# Portfolio Workload Restoration

## Supplied Architecture

The supplied workload is a portfolio-analysis pipeline:

```text
PriceAgent -> MetricsAgent fan-out -> RiskAgent barrier -> AdvisorAgent briefing
```

`PriceAgent` retrieves one ticker's closing-price history and falls back to a
deterministic synthetic series when Yahoo Finance is unavailable. `MetricsAgent`
runs once per holding, calls `PriceAgent`, and computes the supplied per-ticker
return, volatility, Sharpe, drawdown, and raw daily-return series. `RiskAgent`
waits for all ticker metrics and aggregates valid results into portfolio risk.
`AdvisorAgent` makes one final LLM-style briefing call over the already-computed
figures, with a deterministic fallback summary when Bedrock is unavailable.

## Original Infrastructure

The original Ventis version provided infrastructure behavior around the
application:

- remote agent and function registration
- Future-returning agent operations
- fan-out execution
- result transport and serialization
- workflow/REST deployment
- Bedrock-backed LLM telemetry

That explains the original `metric_futures` naming, Future/result handling,
`json.loads()` calls around agent results, agent YAML registration files, and
`self.tools` declarations.

## Clean-Version Adaptation

The clean version no longer includes Ventis, Futures, Redis transport, or the
original deployment wiring. The local restoration therefore replaces only the
necessary execution semantics:

- package-safe imports replace runtime-path-dependent imports
- local Python dicts are passed directly instead of serialized Future results
- `ThreadPoolExecutor` reproduces MetricsAgent fan-out with a local barrier
- RiskAgent still runs only after all ticker metric futures complete
- AdvisorAgent still runs after RiskAgent

No JSON serialization layer was reintroduced for local calls.

## Workload Preservation

The financial calculations and agent responsibilities were intentionally left
unchanged. The portfolio application is the controlled workload for later
Systems benchmarking, so this pass restores local execution without redesigning
the application, replacing formulas, adding autonomous tool selection, or moving
financial work into the LLM stage.

## Benchmark Adapter

`queries.json` contains natural-language query text plus structured metadata,
while the workflow expects structured `{holdings, lookback_days}` input. The
deterministic benchmark adapter converts supplied records into workflow kwargs
using:

- supported `phrasing` values: `percent`, `equal`, `unweighted`
- fixed company-name-to-ticker mappings
- ticker symbols already present in query text
- simple percentage extraction and normalization
- equal weights for equal/unweighted records
- `expected_lookback_days` when supplied

The adapter is not an LLM agent, is not part of model inference cost, and exists
solely to transform supplied benchmark records into the workload input contract.

## Offline Testing

The restored workload can run end to end without external services by using:

- `PriceAgent(use_yfinance=False)` for deterministic synthetic market data
- forced AdvisorAgent Bedrock import failure for deterministic fallback briefing

The tests cover the price fallback, metric calculations, risk aggregation,
Advisor prompt/fallback behavior, deterministic query normalization, fan-out and
barrier ordering, structured workflow smoke execution, and representative real
`queries.json` records through the complete workflow.

Deferred work includes vLLM/open-weight model integration, inference telemetry,
token/cost accounting, GPU monitoring, official 100-query benchmarking, full
1,000-query load testing, and performance/cost analysis.

## Serving Runtime

The synchronous `portfolio_workflow.main()` entry point remains available for
offline compatibility tests and direct local smoke runs. Serving code should use
one process-lifetime `PortfolioRuntime` instead. The runtime owns a shared
bounded `ThreadPoolExecutor`, so concurrent portfolio requests do not each
create their own metric worker pool.

`PortfolioRuntime.analyze()` preserves the workload ordering:

```text
holdings -> MetricsAgent fan-out -> metrics barrier -> RiskAgent -> AdvisorAgent
```

Metric calls run through the shared executor and are limited globally by
`max_concurrent_metric_tasks` across all active requests. All work submitted to
the shared workflow executor, including MetricsAgent and RiskAgent work, is also
bounded by `cpu_workers` so concurrent requests cannot fill an unbounded executor
queue. Risk runs only after every required metric call completes successfully.
Advisor runs only after Risk completes successfully, and the boundary already
prefers an async `summarize_async()` method for the upcoming
vLLM/OpenAI-compatible integration. The temporary synchronous Advisor
compatibility path runs outside the shared workflow CPU executor so it does not
block the asyncio event loop.

The runtime adds only lifecycle and serving-safety plumbing. It does not change
the portfolio calculations, query adapter, price fallback behavior, prompt
construction, or response contract.
