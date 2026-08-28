"""Long-lived serving runtime for the portfolio workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import contextvars
from functools import partial
import threading
from typing import Any

from opentelemetry import context as otel_context

from ..agents.advisor_agent import AdvisorAgent
from ..agents.metrics_agent import MetricsAgent
from ..observability import (
    log_event,
    portfolio_metrics,
    reset_request_context,
    set_request_context,
    start_as_current_span,
)
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
        self._cpu_semaphore = asyncio.Semaphore(config.cpu_workers)
        self._metric_semaphore = asyncio.Semaphore(config.max_concurrent_metric_tasks)
        self._cpu_waiting_count = 0
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
        context_token = set_request_context(context)

        try:
            with start_as_current_span(
                "portfolio.workflow",
                {
                    "stage": "portfolio",
                    "n_holdings": len(tickers),
                    "lookback_days": lookback_days,
                    "run_id": context.run_id,
                    "request_id": context.request_id,
                    "query_id": context.query_id,
                },
            ), portfolio_metrics.workflow_timer():
                return await self._analyze_in_context(
                    holdings=holdings,
                    lookback_days=lookback_days,
                    context=context,
                    tickers=tickers,
                )
        finally:
            reset_request_context(context_token)

    async def _analyze_in_context(
        self,
        *,
        holdings: dict,
        lookback_days: int,
        context: RequestContext,
        tickers: list[str],
    ) -> dict[str, Any]:
        log_event(
            logger_name="portfolio.runtime",
            event="workflow_started",
            context=context,
            stage="portfolio",
            n_holdings=len(tickers),
            lookback_days=lookback_days,
        )
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

        summary = await self._summarize(
            holdings=holdings,
            metrics=per_ticker,
            risk=risk,
            context=context,
        )

        metrics_view = {
            ticker: {
                key: value for key, value in metrics.items() if key != "returns"
            }
            for ticker, metrics in per_ticker.items()
        }
        log_event(
            logger_name="portfolio.runtime",
            event="workflow_completed",
            context=context,
            stage="portfolio",
            status="success",
        )

        return {
            "holdings": holdings,
            "lookback_days": lookback_days,
            "metrics": metrics_view,
            "risk": risk,
            "summary": summary,
        }

    async def close(self) -> None:
        advisor_close = getattr(self.advisor, "aclose", None)
        if advisor_close is not None:
            await advisor_close()
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
        portfolio_metrics.metric_tasks_waiting.inc()
        waiting = True
        try:
            await self._metric_semaphore.acquire()
            portfolio_metrics.metric_tasks_waiting.dec()
            waiting = False
            portfolio_metrics.metric_tasks_running.inc()
            return await self._run_cpu(
                self.metrics_agent.compute,
                acquired_semaphores=(self._metric_semaphore,),
                release_callbacks=(portfolio_metrics.metric_tasks_running.dec,),
                ticker=ticker,
                lookback_days=lookback_days,
            )
        finally:
            if waiting:
                portfolio_metrics.metric_tasks_waiting.dec()

    async def _run_cpu(
        self,
        func,
        /,
        acquired_semaphores=(),
        release_callbacks: tuple[Callable[[], object], ...] = (),
        **kwargs,
    ):
        loop = asyncio.get_running_loop()
        future = None
        released = asyncio.Event()
        release_lock = threading.Lock()
        release_scheduled = False
        cpu_acquired = False
        cpu_waiting = True
        self._cpu_waiting_count += 1
        self._record_cpu_slots()

        def release_resources() -> None:
            nonlocal release_scheduled
            with release_lock:
                if release_scheduled:
                    return
                release_scheduled = True
            loop.call_soon_threadsafe(release_on_loop)

        def release_on_loop() -> None:
            nonlocal cpu_acquired
            if cpu_acquired:
                self._cpu_semaphore.release()
                cpu_acquired = False
            for semaphore in acquired_semaphores:
                semaphore.release()
            for callback in release_callbacks:
                try:
                    callback()
                except Exception:
                    pass
            self._record_cpu_slots()
            released.set()

        try:
            await self._cpu_semaphore.acquire()
            self._cpu_waiting_count -= 1
            cpu_waiting = False
            cpu_acquired = True
            self._record_cpu_slots()
            call = partial(func, **kwargs)
            current_context = otel_context.get_current()
            python_context = contextvars.copy_context()
            future = self._executor.submit(
                _run_with_contexts, current_context, python_context, call
            )
            future.add_done_callback(lambda _future: release_resources())
            wrapped = asyncio.wrap_future(future)
            try:
                result = await wrapped
            except asyncio.CancelledError:
                future.cancel()
                raise
            except BaseException:
                await released.wait()
                raise
            else:
                await released.wait()
                return result
        except asyncio.CancelledError:
            if future is not None:
                future.cancel()
            raise
        finally:
            if future is None:
                if cpu_waiting:
                    self._cpu_waiting_count -= 1
                if cpu_acquired:
                    self._cpu_semaphore.release()
                    cpu_acquired = False
                for semaphore in acquired_semaphores:
                    semaphore.release()
                for callback in release_callbacks:
                    try:
                        callback()
                    except Exception:
                        pass
                self._record_cpu_slots()

    async def _summarize(
        self,
        *,
        holdings: dict,
        metrics: dict,
        risk: dict,
        context: RequestContext,
    ) -> str:
        summarize_async = getattr(self.advisor, "summarize_async", None)
        if summarize_async is not None:
            return await summarize_async(
                holdings=holdings,
                metrics=metrics,
                risk=risk,
                context=context,
            )
        return await self._run_sync_advisor(
            holdings=holdings, metrics=metrics, risk=risk
        )

    async def _run_sync_advisor(self, *, holdings: dict, metrics: dict, risk: dict) -> str:
        loop = asyncio.get_running_loop()
        result_future = loop.create_future()

        def complete_with_result(result: str) -> None:
            try:
                result_future.set_result(result)
            except asyncio.InvalidStateError:
                pass

        def complete_with_error(exc: BaseException) -> None:
            try:
                result_future.set_exception(exc)
            except asyncio.InvalidStateError:
                pass

        def run() -> None:
            try:
                result = self.advisor.summarize(
                    holdings=holdings, metrics=metrics, risk=risk
                )
            except BaseException as exc:
                loop.call_soon_threadsafe(complete_with_error, exc)
            else:
                loop.call_soon_threadsafe(complete_with_result, result)

        thread = threading.Thread(
            target=run,
            name="portfolio-advisor-compat",
            daemon=True,
        )
        thread.start()
        try:
            return await result_future
        finally:
            if not thread.is_alive():
                thread.join()

    def _record_cpu_slots(self) -> None:
        portfolio_metrics.cpu_slots_waiting.set(max(self._cpu_waiting_count, 0))
        used = self.config.cpu_workers - self._cpu_semaphore._value
        portfolio_metrics.cpu_slots_used.set(max(used, 0))


def _run_with_contexts(parent_context, python_context, call):
    token = otel_context.attach(parent_context)
    try:
        return python_context.run(call)
    finally:
        otel_context.detach(token)
