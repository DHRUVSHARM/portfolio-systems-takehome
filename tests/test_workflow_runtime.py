import asyncio
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
import inspect
import threading
import time
import unittest
from unittest.mock import patch

from portfolio.portfolio.service import (
    PortfolioRuntime,
    RequestContext,
    WorkflowRuntimeConfig,
)


def _metric_result(ticker):
    return {
        "ticker": ticker,
        "source": "test",
        "last_price": 100.0,
        "n_days": 3,
        "total_return": 0.02,
        "annualized_return": 0.10,
        "annualized_volatility": 0.20,
        "sharpe": 0.5,
        "max_drawdown": -0.01,
        "returns": [0.01, 0.02],
    }


class EventLog:
    def __init__(self):
        self._events = []
        self._lock = threading.Lock()

    def append(self, *event):
        with self._lock:
            self._events.append(event)

    def snapshot(self):
        with self._lock:
            return list(self._events)


class BarrierMetricsAgent:
    def __init__(self, events, parties):
        self.tools = [self.compute]
        self.events = events
        self.barrier = threading.Barrier(parties, timeout=2.0)

    def compute(self, ticker, lookback_days=365):
        self.events.append("metric-start", ticker)
        self.barrier.wait()
        time.sleep(0.02)
        self.events.append("metric-end", ticker)
        return _metric_result(ticker)


class CountingMetricsAgent:
    def __init__(self, delay=0.02):
        self.tools = [self.compute]
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.calls = 0
        self.lock = threading.Lock()
        self.thread_names = set()

    def compute(self, ticker, lookback_days=365):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
            self.thread_names.add(threading.current_thread().name)
        try:
            time.sleep(self.delay)
            return _metric_result(ticker)
        finally:
            with self.lock:
                self.active -= 1


class TrackingThreadPoolExecutor(RealThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outstanding = 0
        self.peak_outstanding = 0
        self.lock = threading.Lock()

    def submit(self, *args, **kwargs):
        with self.lock:
            self.outstanding += 1
            self.peak_outstanding = max(self.peak_outstanding, self.outstanding)
        future = super().submit(*args, **kwargs)
        future.add_done_callback(self._release_outstanding)
        return future

    def _release_outstanding(self, _future):
        with self.lock:
            self.outstanding -= 1


class FailingMetricsAgent:
    def __init__(self, fail_ticker):
        self.tools = [self.compute]
        self.fail_ticker = fail_ticker

    def compute(self, ticker, lookback_days=365):
        if ticker == self.fail_ticker:
            raise RuntimeError(f"metric failure for {ticker}")
        time.sleep(0.02)
        return _metric_result(ticker)


class BlockingMetricsAgent:
    def __init__(self, events, started, release):
        self.tools = [self.compute]
        self.events = events
        self.started = started
        self.release = release
        self.started_count = 0
        self.lock = threading.Lock()

    def compute(self, ticker, lookback_days=365):
        self.events.append("metric-start", ticker)
        with self.lock:
            self.started_count += 1
            if self.started_count == 2:
                self.started.set()
        self.release.wait(timeout=2.0)
        self.events.append("metric-end", ticker)
        return _metric_result(ticker)


class RecordingRiskAgent:
    def __init__(self, events=None, fail=False, delay=0.0):
        self.tools = [self.assess]
        self.events = events or EventLog()
        self.fail = fail
        self.delay = delay
        self.called = False
        self.calls = 0
        self.received_metrics = None

    def assess(self, holdings, metrics):
        self.called = True
        self.calls += 1
        self.received_metrics = metrics
        self.events.append("risk-start", tuple(metrics))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("risk failure")
        return {
            "n_holdings": len(metrics),
            "weights": holdings,
            "portfolio_annualized_return": 0.10,
            "portfolio_annualized_volatility": 0.20,
            "portfolio_sharpe": 0.5,
            "concentration_hhi": 0.34,
            "diversification_ratio": 1.0,
            "top_holding": {
                "ticker": next(iter(holdings)),
                "weight": next(iter(holdings.values())),
            },
        }


class RecordingAdvisorAgent:
    def __init__(self, events=None):
        self.events = events or EventLog()
        self.called = False

    async def summarize_async(self, holdings, metrics, risk, context=None):
        del context
        self.called = True
        self.events.append("advisor-start", tuple(metrics))
        return "async summary"


class BlockingSyncAdvisorAgent:
    def __init__(self, delay=0.15):
        self.delay = delay
        self.start_time = None
        self.end_time = None

    def summarize(self, holdings, metrics, risk):
        del holdings, metrics, risk
        self.start_time = time.monotonic()
        time.sleep(self.delay)
        self.end_time = time.monotonic()
        return "sync summary"


class WorkflowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def asyncTearDown(self):
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            await runtime.close()
        self._heartbeat_task.cancel()
        await asyncio.gather(self._heartbeat_task, return_exceptions=True)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(0.001)

    async def test_fanout_barrier_and_advisor_order(self):
        events = EventLog()
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=3, max_concurrent_metric_tasks=3),
            metrics_agent=BarrierMetricsAgent(events, parties=3),
            risk_agent=RecordingRiskAgent(events),
            advisor=RecordingAdvisorAgent(events),
        )

        result = await self.runtime.analyze(
            holdings={"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
            lookback_days=180,
            context=RequestContext(run_id="test-run"),
        )

        snapshot = events.snapshot()
        first_end = next(i for i, event in enumerate(snapshot) if event[0] == "metric-end")
        risk_index = next(i for i, event in enumerate(snapshot) if event[0] == "risk-start")
        advisor_index = next(
            i for i, event in enumerate(snapshot) if event[0] == "advisor-start"
        )
        starts_before_first_end = [
            event for event in snapshot[:first_end] if event[0] == "metric-start"
        ]
        metric_end_indexes = [
            i for i, event in enumerate(snapshot) if event[0] == "metric-end"
        ]

        self.assertEqual(len(starts_before_first_end), 3)
        self.assertTrue(all(index < risk_index for index in metric_end_indexes))
        self.assertLess(risk_index, advisor_index)
        self.assertEqual(result["summary"], "async summary")

    async def test_global_metric_task_bound_across_concurrent_requests(self):
        metrics_agent = CountingMetricsAgent(delay=0.03)
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=8, max_concurrent_metric_tasks=4),
            metrics_agent=metrics_agent,
            risk_agent=RecordingRiskAgent(),
            advisor=RecordingAdvisorAgent(),
        )
        holdings = {f"T{i}": 1 / 6 for i in range(6)}

        await asyncio.gather(
            *[
                self.runtime.analyze(holdings=holdings, lookback_days=30)
                for _ in range(10)
            ]
        )

        self.assertEqual(metrics_agent.calls, 60)
        self.assertLessEqual(metrics_agent.peak, 4)

    async def test_shared_runtime_reuses_one_executor_across_requests(self):
        created_executors = []

        def executor_factory(*args, **kwargs):
            executor = RealThreadPoolExecutor(*args, **kwargs)
            created_executors.append(executor)
            return executor

        with patch(
            "portfolio.portfolio.service.runtime.ThreadPoolExecutor",
            side_effect=executor_factory,
        ):
            metrics_agent = CountingMetricsAgent(delay=0.01)
            self.runtime = PortfolioRuntime(
                config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
                metrics_agent=metrics_agent,
                risk_agent=RecordingRiskAgent(),
                advisor=RecordingAdvisorAgent(),
            )
            holdings = {"AAPL": 0.5, "MSFT": 0.5}

            await self.runtime.analyze(holdings=holdings, lookback_days=30)
            await self.runtime.analyze(holdings=holdings, lookback_days=30)

        self.assertEqual(len(created_executors), 1)
        self.assertEqual(metrics_agent.calls, 4)

    async def test_total_cpu_admission_bound_includes_metrics_and_risk(self):
        created_executors = []

        def executor_factory(*args, **kwargs):
            executor = TrackingThreadPoolExecutor(*args, **kwargs)
            created_executors.append(executor)
            return executor

        with patch(
            "portfolio.portfolio.service.runtime.ThreadPoolExecutor",
            side_effect=executor_factory,
        ):
            metrics_agent = CountingMetricsAgent(delay=0.02)
            risk_agent = RecordingRiskAgent(delay=0.04)
            self.runtime = PortfolioRuntime(
                config=WorkflowRuntimeConfig(cpu_workers=3, max_concurrent_metric_tasks=12),
                metrics_agent=metrics_agent,
                risk_agent=risk_agent,
                advisor=RecordingAdvisorAgent(),
            )
            holdings = {f"T{i}": 0.25 for i in range(4)}

            await asyncio.gather(
                *[
                    self.runtime.analyze(holdings=holdings, lookback_days=30)
                    for _ in range(8)
                ]
            )

        self.assertEqual(len(created_executors), 1)
        self.assertEqual(metrics_agent.calls, 32)
        self.assertEqual(risk_agent.calls, 8)
        self.assertLessEqual(created_executors[0].peak_outstanding, 3)

    async def test_metrics_failure_prevents_risk_and_advisor(self):
        risk_agent = RecordingRiskAgent()
        advisor = RecordingAdvisorAgent()
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=3, max_concurrent_metric_tasks=3),
            metrics_agent=FailingMetricsAgent(fail_ticker="MSFT"),
            risk_agent=risk_agent,
            advisor=advisor,
        )

        with self.assertRaisesRegex(RuntimeError, "metric failure"):
            await self.runtime.analyze(
                holdings={"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
                lookback_days=180,
            )

        self.assertFalse(risk_agent.called)
        self.assertFalse(advisor.called)

    async def test_risk_failure_prevents_advisor(self):
        advisor = RecordingAdvisorAgent()
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=CountingMetricsAgent(delay=0.01),
            risk_agent=RecordingRiskAgent(fail=True),
            advisor=advisor,
        )

        with self.assertRaisesRegex(RuntimeError, "risk failure"):
            await self.runtime.analyze(
                holdings={"AAPL": 0.5, "MSFT": 0.5},
                lookback_days=180,
            )

        self.assertFalse(advisor.called)

    async def test_cancellation_prevents_risk_and_advisor(self):
        events = EventLog()
        started = threading.Event()
        release = threading.Event()
        risk_agent = RecordingRiskAgent(events)
        advisor = RecordingAdvisorAgent(events)
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=BlockingMetricsAgent(events, started, release),
            risk_agent=risk_agent,
            advisor=advisor,
        )

        task = asyncio.create_task(
            self.runtime.analyze(
                holdings={"AAPL": 0.5, "MSFT": 0.5},
                lookback_days=180,
            )
        )
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(started.is_set())
        task.cancel()
        cancelled_started = time.perf_counter()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.perf_counter() - cancelled_started, 0.1)

        self.assertFalse(risk_agent.called)
        self.assertFalse(advisor.called)
        self.assertEqual(self.runtime._cpu_semaphore._value, 0)
        self.assertEqual(self.runtime._metric_semaphore._value, 0)
        release.set()
        for _ in range(100):
            if (
                self.runtime._cpu_semaphore._value == 2
                and self.runtime._metric_semaphore._value == 2
            ):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.runtime._cpu_semaphore._value, 2)
        self.assertEqual(self.runtime._metric_semaphore._value, 2)

        result = await self.runtime.analyze(
            holdings={"GOOGL": 1.0},
            lookback_days=180,
        )
        self.assertEqual(result["summary"], "async summary")

    async def test_executor_future_bridge_does_not_poll(self):
        source = inspect.getsource(PortfolioRuntime._run_cpu)

        self.assertIn("asyncio.wrap_future", source)
        self.assertNotIn("while not future.done()", source)
        self.assertNotIn("asyncio.sleep(0.001)", source)

    async def test_z_sync_advisor_compatibility_does_not_block_event_loop(self):
        advisor = BlockingSyncAdvisorAgent(delay=0.15)
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=CountingMetricsAgent(delay=0.01),
            risk_agent=RecordingRiskAgent(),
            advisor=advisor,
        )
        tick_times = []
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                tick_times.append(time.monotonic())
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await self.runtime.analyze(
                holdings={"AAPL": 0.5, "MSFT": 0.5},
                lookback_days=90,
            )
        finally:
            stop.set()
            await asyncio.gather(ticker_task, return_exceptions=True)

        self.assertEqual(result["summary"], "sync summary")
        ticks_during_advisor = [
            tick
            for tick in tick_times
            if advisor.start_time <= tick <= advisor.end_time
        ]
        self.assertGreaterEqual(len(ticks_during_advisor), 5)

    async def test_response_compatibility_and_internal_returns_for_risk(self):
        risk_agent = RecordingRiskAgent()
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=CountingMetricsAgent(delay=0.01),
            risk_agent=risk_agent,
            advisor=RecordingAdvisorAgent(),
        )

        result = await self.runtime.analyze(
            holdings={"AAPL": 0.5, "MSFT": 0.5},
            lookback_days=90,
        )

        self.assertEqual(
            sorted(result),
            ["holdings", "lookback_days", "metrics", "risk", "summary"],
        )
        self.assertEqual(result["lookback_days"], 90)
        self.assertTrue(
            all("returns" not in metric for metric in result["metrics"].values())
        )
        self.assertIsNotNone(risk_agent.received_metrics)
        self.assertTrue(
            all("returns" in metric for metric in risk_agent.received_metrics.values())
        )

    async def test_config_requires_positive_integers(self):
        with self.assertRaises(ValueError):
            WorkflowRuntimeConfig(cpu_workers=0, max_concurrent_metric_tasks=1)
        with self.assertRaises(ValueError):
            WorkflowRuntimeConfig(cpu_workers=1, max_concurrent_metric_tasks=0)


if __name__ == "__main__":
    unittest.main()
