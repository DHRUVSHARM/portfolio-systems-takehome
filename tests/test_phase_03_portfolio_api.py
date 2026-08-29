from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import patch

import httpx

from portfolio.portfolio.service import RequestContext
from services.portfolio_api import PortfolioApiConfig, build_portfolio_runtime, create_app


BUSINESS_RESPONSE = {
    "holdings": {"AAPL": 0.6, "MSFT": 0.4},
    "lookback_days": 30,
    "metrics": {
        "AAPL": {"ticker": "AAPL", "annualized_return": 0.1},
        "MSFT": {"ticker": "MSFT", "annualized_return": 0.2},
    },
    "risk": {"portfolio_sharpe": 1.2},
    "summary": "Generated summary.",
}


class FakeRuntime:
    def __init__(self, *, result=None, failure=None):
        self.result = result or BUSINESS_RESPONSE
        self.failure = failure
        self.calls = []
        self.closed = False

    async def analyze(self, *, holdings, lookback_days=365, context=None):
        self.calls.append(
            {
                "holdings": holdings,
                "lookback_days": lookback_days,
                "context": context,
            }
        )
        if self.failure is not None:
            raise self.failure
        return self.result

    async def close(self):
        self.closed = True


class Phase3PortfolioApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_request_invokes_runtime_once_and_preserves_response_shape(self):
        runtime = FakeRuntime()

        async with _client_for(runtime) as client:
            response = await client.post(
                "/internal/analyze",
                json={"holdings": {"AAPL": 0.6, "MSFT": 0.4}, "lookback_days": 30},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["holdings"], {"AAPL": 0.6, "MSFT": 0.4})
        self.assertEqual(runtime.calls[0]["lookback_days"], 30)
        self.assertIsInstance(runtime.calls[0]["context"], RequestContext)
        self.assertEqual(response.json(), BUSINESS_RESPONSE)
        self.assertNotIn("request_id", response.json())
        self.assertNotIn("run_id", response.json())
        self.assertNotIn("query_id", response.json())

    async def test_header_ids_form_request_context_and_response_headers(self):
        runtime = FakeRuntime()

        async with _client_for(runtime) as client:
            response = await client.post(
                "/internal/analyze",
                headers={
                    "X-Run-ID": "run-1",
                    "X-Request-ID": "request-1",
                    "X-Query-ID": "query-1",
                },
                json={"holdings": {"AAPL": 1.0}},
            )

        context = runtime.calls[0]["context"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(context.run_id, "run-1")
        self.assertEqual(context.request_id, "request-1")
        self.assertEqual(context.query_id, "query-1")
        self.assertEqual(response.headers["X-Run-ID"], "run-1")
        self.assertEqual(response.headers["X-Request-ID"], "request-1")
        self.assertEqual(response.headers["X-Query-ID"], "query-1")

    async def test_missing_request_id_generates_one(self):
        runtime = FakeRuntime()

        async with _client_for(runtime) as client:
            response = await client.post(
                "/internal/analyze",
                headers={"X-Run-ID": "run-1"},
                json={"holdings": {"AAPL": 1.0}},
            )

        request_id = runtime.calls[0]["context"].request_id
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(request_id, str)
        self.assertGreater(len(request_id), 0)
        self.assertEqual(response.headers["X-Request-ID"], request_id)
        self.assertEqual(runtime.calls[0]["context"].run_id, "run-1")

    async def test_invalid_holdings_rejected(self):
        invalid_payloads = [
            {"holdings": {}},
            {"holdings": {"": 1.0}},
            {"holdings": {"AAPL": 0}},
            {"holdings": {"AAPL": -0.1}},
            {"holdings": {"AAPL": "heavy"}},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                runtime = FakeRuntime()
                async with _client_for(runtime) as client:
                    response = await client.post("/internal/analyze", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(runtime.calls, [])

    async def test_invalid_lookback_rejected(self):
        invalid_payloads = [
            {"holdings": {"AAPL": 1.0}, "lookback_days": 0},
            {"holdings": {"AAPL": 1.0}, "lookback_days": -1},
            {"holdings": {"AAPL": 1.0}, "lookback_days": "30"},
            {"holdings": {"AAPL": 1.0}, "lookback_days": True},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                runtime = FakeRuntime()
                async with _client_for(runtime) as client:
                    response = await client.post("/internal/analyze", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(runtime.calls, [])

    async def test_runtime_failure_returns_controlled_non_success_response(self):
        runtime = FakeRuntime(failure=RuntimeError("raw secret stack detail"))

        async with _client_for(runtime) as client:
            response = await client.post(
                "/internal/analyze",
                json={"holdings": {"AAPL": 1.0}, "lookback_days": 30},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"detail": "portfolio workflow failed"})
        self.assertNotIn("secret", response.text)

    async def test_health_works_independently_of_observability(self):
        async with _client_for(FakeRuntime()) as client:
            response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_ready_reflects_initialization_state(self):
        app = _app_for(FakeRuntime())

        async with _lifespan_client(app) as client:
            ready = await client.get("/ready")

        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"status": "ready"})
        self.assertFalse(app.state.ready)

    async def test_uninitialized_ready_returns_503(self):
        app = _app_for(FakeRuntime())
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/ready")

        self.assertFalse(app.state.ready)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "service is not ready"})

    async def test_process_lifespan_creates_one_runtime_and_reuses_it(self):
        created = []

        def factory(config):
            runtime = FakeRuntime()
            created.append(runtime)
            return runtime

        app = create_app(config=PortfolioApiConfig(), runtime_factory=factory)

        async with _lifespan_client(app) as client:
            first = await client.post(
                "/internal/analyze", json={"holdings": {"AAPL": 1.0}}
            )
            second = await client.post(
                "/internal/analyze", json={"holdings": {"MSFT": 1.0}}
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(created[0].calls), 2)

    async def test_shutdown_closes_owned_resources(self):
        runtime = FakeRuntime()

        async with _client_for(runtime) as client:
            response = await client.get("/ready")
            self.assertEqual(response.status_code, 200)

        self.assertTrue(runtime.closed)

    async def test_benchmark_deterministic_price_mode_can_be_selected(self):
        runtime = build_portfolio_runtime(
            PortfolioApiConfig(
                use_yfinance=False,
                cpu_workers=1,
                max_concurrent_metric_tasks=1,
            )
        )
        try:
            self.assertFalse(runtime.metrics_agent.price.use_yfinance)
        finally:
            await runtime.close()

    async def test_optional_inference_enable_thinking_parses_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(
                PortfolioApiConfig.from_env().inference_enable_thinking
            )

        for raw, expected in (
            ("", None),
            ("   ", None),
            ("true", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("0", False),
            ("no", False),
            ("off", False),
        ):
            with self.subTest(raw=raw), patch.dict(
                os.environ,
                {"PORTFOLIO_INFERENCE_ENABLE_THINKING": raw},
                clear=True,
            ):
                self.assertIs(
                    PortfolioApiConfig.from_env().inference_enable_thinking,
                    expected,
                )

        with patch.dict(
            os.environ,
            {"PORTFOLIO_INFERENCE_ENABLE_THINKING": "maybe"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                PortfolioApiConfig.from_env()

    async def test_build_runtime_passes_inference_enable_thinking_to_client_config(self):
        runtime = build_portfolio_runtime(
            PortfolioApiConfig(
                cpu_workers=1,
                max_concurrent_metric_tasks=1,
                inference_enable_thinking=False,
            )
        )
        try:
            self.assertIs(runtime.advisor.client.config.enable_thinking, False)
        finally:
            await runtime.close()


def _app_for(runtime):
    return create_app(
        config=PortfolioApiConfig(),
        runtime_factory=lambda config: runtime,
    )


@asynccontextmanager
async def _client_for(runtime):
    async with _lifespan_client(_app_for(runtime)) as client:
        yield client


@asynccontextmanager
async def _lifespan_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


if __name__ == "__main__":
    unittest.main()
