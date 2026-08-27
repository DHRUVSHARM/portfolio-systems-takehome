"""Long-lived serving runtime for the portfolio workflow."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from ..agents.advisor_agent import AdvisorAgent
from ..agents.metrics_agent import MetricsAgent
from ..agents.risk_agent import RiskAgent
from .config import WorkflowRuntimeConfig
from .context import RequestContext


class PortfolioRuntime:
    """Run portfolio analyses with a shared bounded execution runtime."""

    def __init__(
        self,
        *,
        config: WorkflowRuntimeConfig,
        metrics_agent: MetricsAgent | None = None,
        risk_agent: RiskAgent | None = None,
        advisor: AdvisorAgent | None = None,
    ) -> None:
        self.config = config
        self.metrics_agent = metrics_agent or MetricsAgent()
        self.risk_agent = risk_agent or RiskAgent()
        self.advisor = advisor or AdvisorAgent()
        self._executor = ThreadPoolExecutor(max_workers=config.cpu_workers)
        self._metric_semaphore = asyncio.Semaphore(config.max_concurrent_metric_tasks)
        self._closed = False

    async def analyze(
        self,
        *,
        holdings: dict,
        lookback_days: int = 365,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """Analyze one portfolio while preserving fan-out/barrier semantics."""
        if self._closed:
            raise RuntimeError("PortfolioRuntime is closed")

        context = context or RequestContext()
        tickers = list(holdings.keys())

        metric_tasks = [
            asyncio.create_task(
                self._compute_metric(ticker, lookback_days, context),
                name=f"portfolio-metric-{ticker}",
            )
            for ticker in tickers
        ]

        try:
            metric_results = await asyncio.gather(*metric_tasks)
        except BaseException:
            for task in metric_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*metric_tasks, return_exceptions=True)
            raise

        per_ticker = dict(zip(tickers, metric_results))

        risk = await self._run_cpu(
            self.risk_agent.assess, holdings=holdings, metrics=per_ticker
        )

        summary = await self._summarize(holdings=holdings, metrics=per_ticker, risk=risk)

        metrics_view = {
            ticker: {
                key: value for key, value in metrics.items() if key != "returns"
            }
            for ticker, metrics in per_ticker.items()
        }

        return {
            "holdings": holdings,
            "lookback_days": lookback_days,
            "metrics": metrics_view,
            "risk": risk,
            "summary": summary,
        }

    async def close(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "PortfolioRuntime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown()

    async def __aenter__(self) -> "PortfolioRuntime":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def _compute_metric(
        self, ticker: str, lookback_days: int, context: RequestContext
    ) -> dict:
        del context
        await self._metric_semaphore.acquire()
        loop = asyncio.get_running_loop()
        future = self._executor.submit(
            partial(
                self.metrics_agent.compute,
                ticker=ticker,
                lookback_days=lookback_days,
            )
        )
        try:
            return await self._await_executor_future(future)
        finally:
            self._release_metric_slot_when_done(future, loop)

    async def _run_cpu(self, func, /, **kwargs):
        future = self._executor.submit(partial(func, **kwargs))
        return await self._await_executor_future(future)

    async def _summarize(self, *, holdings: dict, metrics: dict, risk: dict) -> str:
        summarize_async = getattr(self.advisor, "summarize_async", None)
        if summarize_async is not None:
            return await summarize_async(holdings=holdings, metrics=metrics, risk=risk)
        return self.advisor.summarize(holdings=holdings, metrics=metrics, risk=risk)

    def _release_metric_slot_when_done(
        self, future, loop: asyncio.AbstractEventLoop
    ) -> None:
        if future.done():
            self._metric_semaphore.release()
            return

        def release_slot(_future) -> None:
            loop.call_soon_threadsafe(self._metric_semaphore.release)

        future.add_done_callback(release_slot)

    async def _await_executor_future(self, future):
        while not future.done():
            await asyncio.sleep(0.001)
        return future.result()
