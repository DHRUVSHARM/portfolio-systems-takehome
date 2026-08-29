import asyncio
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from unittest.mock import patch

import httpx

from portfolio.portfolio.agents.vllm_advisor_agent import VLLMAdvisorAgent
from portfolio.portfolio.inference import (
    InferenceClientConfig,
    InferenceHTTPStatusError,
    OpenAICompatibleInferenceClient,
)
from portfolio.portfolio.service import (
    PortfolioRuntime,
    RequestContext,
    WorkflowRuntimeConfig,
)


def completion_response(text="Generated summary.", model="Qwen/Qwen3-4B-Instruct-2507"):
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 21,
                "completion_tokens": 7,
                "total_tokens": 28,
            },
        },
    )


class FakeMetricsAgent:
    def __init__(self):
        self.tools = [self.compute]

    def compute(self, ticker, lookback_days=365):
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


class FakeRiskAgent:
    def __init__(self):
        self.tools = [self.assess]

    def assess(self, holdings, metrics):
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


class TrackingThreadPoolExecutor(RealThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submit_count = 0
        self.lock = threading.Lock()

    def submit(self, *args, **kwargs):
        with self.lock:
            self.submit_count += 1
        return super().submit(*args, **kwargs)


class Phase2InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def asyncTearDown(self):
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            await runtime.close()
        client = getattr(self, "client", None)
        if client is not None:
            await client.aclose()
        self._heartbeat_task.cancel()
        await asyncio.gather(self._heartbeat_task, return_exceptions=True)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(0.001)

    async def test_openai_compatible_request_shape(self):
        seen = []

        async def handler(request):
            seen.append(request)
            payload = json.loads(request.content)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(payload["model"], "Qwen/Qwen3-4B-Instruct-2507")
            self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
            self.assertEqual(payload["temperature"], 0.0)
            self.assertEqual(payload["max_tokens"], 256)
            self.assertFalse(payload["stream"])
            self.assertNotIn("chat_template_kwargs", payload)
            return completion_response()

        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(handler),
        )

        await self.client.chat_completion("prompt")

        self.assertEqual(len(seen), 1)

    async def test_openai_compatible_request_adds_thinking_override_only_when_configured(self):
        for value in (True, False):
            with self.subTest(enable_thinking=value):
                seen = []

                async def handler(request):
                    seen.append(json.loads(request.content))
                    return completion_response()

                client = OpenAICompatibleInferenceClient(
                    InferenceClientConfig(
                        base_url="http://vllm:8000/v1",
                        enable_thinking=value,
                    ),
                    transport=httpx.MockTransport(handler),
                )
                try:
                    await client.chat_completion("prompt")
                finally:
                    await client.aclose()

                self.assertEqual(
                    seen[0]["chat_template_kwargs"],
                    {"enable_thinking": value},
                )

    async def test_response_extraction_and_usage_capture(self):
        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(lambda request: completion_response("Briefing.")),
        )

        result = await self.client.chat_completion("prompt")

        self.assertEqual(result.text, "Briefing.")
        self.assertEqual(result.model, "Qwen/Qwen3-4B-Instruct-2507")
        self.assertEqual(result.prompt_tokens, 21)
        self.assertEqual(result.completion_tokens, 7)
        self.assertEqual(result.total_tokens, 28)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.retry_count, 0)
        self.assertGreaterEqual(result.elapsed_ms, 0.0)

    async def test_request_correlation_reaches_observation_sink(self):
        observations = []
        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(lambda request: completion_response()),
        )
        advisor = VLLMAdvisorAgent(client=self.client, observation_sink=observations.append)

        summary = await advisor.summarize_async(
            holdings={"AAPL": 1.0},
            metrics={"AAPL": _metric("AAPL")},
            risk=_risk(),
            context=RequestContext(
                run_id="run-1", request_id="request-1", query_id="query-1"
            ),
        )

        self.assertEqual(summary, "Generated summary.")
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].run_id, "run-1")
        self.assertEqual(observations[0].request_id, "request-1")
        self.assertEqual(observations[0].query_id, "query-1")
        self.assertEqual(observations[0].result.total_tokens, 28)

    async def test_http_500_propagates_and_retry_count_zero_makes_one_attempt(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, text="server error")

        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1", retry_count=0),
            transport=httpx.MockTransport(handler),
        )
        advisor = VLLMAdvisorAgent(client=self.client)
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=FakeMetricsAgent(),
            risk_agent=FakeRiskAgent(),
            advisor=advisor,
        )

        with self.assertRaises(InferenceHTTPStatusError):
            await self.runtime.analyze(
                holdings={"AAPL": 0.5, "MSFT": 0.5},
                lookback_days=30,
            )

        self.assertEqual(attempts, 1)

    async def test_client_reuse_and_lifecycle(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return completion_response(f"Summary {attempts}.")

        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(handler),
        )
        advisor = VLLMAdvisorAgent(client=self.client)

        first = await advisor.summarize_async({"AAPL": 1.0}, {"AAPL": _metric("AAPL")}, _risk())
        second = await advisor.summarize_async({"AAPL": 1.0}, {"AAPL": _metric("AAPL")}, _risk())
        await advisor.aclose()

        self.assertEqual(first, "Summary 1.")
        self.assertEqual(second, "Summary 2.")
        self.assertEqual(attempts, 2)
        with self.assertRaises(RuntimeError):
            await advisor.summarize_async(
                {"AAPL": 1.0}, {"AAPL": _metric("AAPL")}, _risk()
            )

    async def test_runtime_response_compatibility_hides_inference_telemetry(self):
        observations = []
        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(lambda request: completion_response("Runtime summary.")),
        )
        advisor = VLLMAdvisorAgent(client=self.client, observation_sink=observations.append)
        self.runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=FakeMetricsAgent(),
            risk_agent=FakeRiskAgent(),
            advisor=advisor,
        )

        result = await self.runtime.analyze(
            holdings={"AAPL": 0.5, "MSFT": 0.5},
            lookback_days=90,
            context=RequestContext(run_id="run", request_id="request", query_id="query"),
        )

        self.assertEqual(sorted(result), ["holdings", "lookback_days", "metrics", "risk", "summary"])
        self.assertEqual(result["summary"], "Runtime summary.")
        self.assertTrue(all("returns" not in metric for metric in result["metrics"].values()))
        self.assertNotIn("prompt_tokens", result)
        self.assertEqual(len(observations), 1)

    async def test_delayed_inference_does_not_block_loop_or_workflow_executor(self):
        ticks = []
        created_executors = []

        async def handler(request):
            await asyncio.sleep(0.05)
            return completion_response("Delayed summary.")

        async def ticker(stop):
            while not stop.is_set():
                ticks.append(time.monotonic())
                await asyncio.sleep(0.005)

        def executor_factory(*args, **kwargs):
            executor = TrackingThreadPoolExecutor(*args, **kwargs)
            created_executors.append(executor)
            return executor

        self.client = OpenAICompatibleInferenceClient(
            InferenceClientConfig(base_url="http://vllm:8000/v1"),
            transport=httpx.MockTransport(handler),
        )
        advisor = VLLMAdvisorAgent(client=self.client)
        stop = asyncio.Event()
        ticker_task = asyncio.create_task(ticker(stop))

        with patch(
            "portfolio.portfolio.service.runtime.ThreadPoolExecutor",
            side_effect=executor_factory,
        ):
            self.runtime = PortfolioRuntime(
                config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
                metrics_agent=FakeMetricsAgent(),
                risk_agent=FakeRiskAgent(),
                advisor=advisor,
            )
            result = await self.runtime.analyze(
                holdings={"AAPL": 0.5, "MSFT": 0.5},
                lookback_days=90,
            )

        stop.set()
        await asyncio.gather(ticker_task, return_exceptions=True)

        self.assertEqual(result["summary"], "Delayed summary.")
        self.assertGreaterEqual(len(ticks), 5)
        self.assertEqual(len(created_executors), 1)
        self.assertEqual(created_executors[0].submit_count, 3)


def _metric(ticker):
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


def _risk():
    return {
        "n_holdings": 1,
        "weights": {"AAPL": 1.0},
        "portfolio_annualized_return": 0.10,
        "portfolio_annualized_volatility": 0.20,
        "portfolio_sharpe": 0.5,
        "concentration_hhi": 1.0,
        "diversification_ratio": 1.0,
        "top_holding": {"ticker": "AAPL", "weight": 1.0},
    }


if __name__ == "__main__":
    unittest.main()
