import asyncio
from contextlib import asynccontextmanager, contextmanager
import io
import json
import logging
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from portfolio.portfolio.agents.metrics_agent import MetricsAgent
from portfolio.portfolio.agents.risk_agent import RiskAgent
from portfolio.portfolio.agents.vllm_advisor_agent import VLLMAdvisorAgent
from portfolio.portfolio.inference import InferenceClientConfig, OpenAICompatibleInferenceClient
from portfolio.portfolio.observability import (
    ObservabilityConfig,
    configure_tracing,
    gateway_metrics,
    get_finished_spans,
    portfolio_metrics,
    reset_observability_for_tests,
    start_as_current_span,
)
from portfolio.portfolio.observability.logging import JsonFormatter
from portfolio.portfolio.service import PortfolioRuntime, WorkflowRuntimeConfig
from services.gateway import GatewayConfig, create_app as create_gateway_app
from services.gateway.client import DownstreamResult
from portfolio.portfolio.service import RequestContext


ROOT = Path(__file__).resolve().parents[1]


class StaticPriceAgent:
    def __init__(self):
        self.tools = [self.get_history]
        self.use_yfinance = False

    def get_history(self, ticker, lookback_days=365):
        return {
            "ticker": ticker,
            "dates": [],
            "closes": [100.0 + index for index in range(max(lookback_days, 3))],
            "source": "test",
        }


class RuntimePortfolioClient:
    def __init__(self, runtime):
        self.runtime = runtime
        self.closed = False

    async def analyze(self, *, payload, headers):
        with start_as_current_span(
            "portfolio.request",
            {
                "stage": "portfolio",
                "n_holdings": len(payload["holdings"]),
                "lookback_days": payload["lookback_days"],
            },
        ):
            with start_as_current_span(
                "POST /internal/analyze",
                {
                    "http.route": "/internal/analyze",
                    "run_id": headers.get("X-Run-ID"),
                    "request_id": headers.get("X-Request-ID"),
                    "query_id": headers.get("X-Query-ID"),
                },
            ):
                body = await self.runtime.analyze(
                    holdings=payload["holdings"],
                    lookback_days=payload["lookback_days"],
                    context=RequestContext(
                        run_id=headers.get("X-Run-ID"),
                        request_id=headers.get("X-Request-ID"),
                        query_id=headers.get("X-Query-ID"),
                    ),
                )
        return DownstreamResult(status_code=200, body=body, headers=headers)

    async def aclose(self):
        self.closed = True
        await self.runtime.close()


class Phase6ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_observability_for_tests()
        configure_tracing(
            ObservabilityConfig(service_name="phase6-test", tracing_sample_ratio=1.0),
            in_memory=True,
        )

    async def test_metrics_labels_exclude_high_cardinality_ids_and_ticker(self):
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            response = await _post_gateway(gateway_client)

        self.assertEqual(response.status_code, 200)
        from portfolio.portfolio.observability import render_gateway_metrics

        metrics_text = render_gateway_metrics().decode() + "\n" + portfolio_metrics_text()
        self.assertNotIn("request_id=", metrics_text)
        self.assertNotIn("query_id=", metrics_text)
        self.assertNotIn("run_id=", metrics_text)
        self.assertNotIn("trace_id=", metrics_text)
        self.assertNotIn("ticker=", metrics_text)

    async def test_gateway_metrics_update_correctly(self):
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            await _post_gateway(gateway_client)
            metrics = (await gateway_client.get("/metrics")).text

        self.assertIn("gateway_requests_total", metrics)
        self.assertIn('endpoint="/v1/analyze"', metrics)
        self.assertIn('status_code="200"', metrics)
        self.assertIn("gateway_queue_wait_duration_seconds", metrics)
        self.assertIn("gateway_downstream_duration_seconds", metrics)

    async def test_agent_and_tool_metrics_distinguish_required_components(self):
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            await _post_gateway(gateway_client)

        metrics = portfolio_metrics_text()
        for agent in ["MetricsAgent", "PriceAgent", "RiskAgent", "AdvisorAgent"]:
            self.assertIn(f'agent="{agent}"', metrics)
        for tool in ["get_history", "assess", "summarize"]:
            self.assertIn(f'tool="{tool}"', metrics)

    async def test_trace_structure_and_executor_thread_parent_context(self):
        before = len(get_finished_spans())
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            response = await _post_gateway(gateway_client)

        self.assertEqual(response.status_code, 200)
        spans = get_finished_spans()[before:]
        by_name = {span.name: span for span in spans}
        required = [
            "POST /v1/analyze",
            "gateway.validation",
            "gateway.admission_wait",
            "portfolio.request",
            "POST /internal/analyze",
            "portfolio.workflow",
            "MetricsAgent[AAPL]",
            "PriceAgent.get_history[AAPL]",
            "RiskAgent.assess",
            "AdvisorAgent.summarize",
            "inference.request",
        ]
        for name in required:
            self.assertIn(name, by_name)

        trace_ids = {span.context.trace_id for span in spans}
        self.assertEqual(len(trace_ids), 1)
        self.assertEqual(
            by_name["MetricsAgent[AAPL]"].parent.span_id,
            by_name["portfolio.workflow"].context.span_id,
        )
        self.assertEqual(
            by_name["PriceAgent.get_history[AAPL]"].parent.span_id,
            by_name["MetricsAgent[AAPL]"].context.span_id,
        )

    async def test_request_context_ids_appear_as_trace_and_log_fields(self):
        before = len(get_finished_spans())
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            await _post_gateway(gateway_client)

        spans = get_finished_spans()[before:]
        attrs = [span.attributes for span in spans]
        self.assertTrue(any(item.get("run_id") == "run-obs" for item in attrs))
        self.assertTrue(any(item.get("request_id") == "request-obs" for item in attrs))
        self.assertTrue(any(item.get("query_id") == "query-obs" for item in attrs))

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter(service="test-service"))
        logger = logging.getLogger("phase6-log-test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        with start_as_current_span("log-span"):
            logger.info(
                "structured_event",
                extra={
                    "structured": {
                        "run_id": "run-obs",
                        "request_id": "request-obs",
                        "query_id": "query-obs",
                    }
                },
            )
        row = json.loads(stream.getvalue())
        self.assertEqual(row["run_id"], "run-obs")
        self.assertEqual(row["request_id"], "request-obs")
        self.assertEqual(row["query_id"], "query-obs")
        self.assertIn("trace_id", row)
        self.assertIn("span_id", row)

    async def test_dynamic_span_attributes_are_recorded(self):
        before = len(get_finished_spans())
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            response = await _post_gateway(gateway_client)

        self.assertEqual(response.status_code, 200)
        spans = get_finished_spans()[before:]
        by_name = {span.name: span for span in spans}
        root_attrs = by_name["POST /v1/analyze"].attributes
        inference_attrs = by_name["inference.request"].attributes

        self.assertEqual(root_attrs["n_holdings"], 1)
        self.assertEqual(root_attrs["lookback_days"], 30)
        self.assertEqual(inference_attrs["http.status_code"], 200)
        self.assertEqual(inference_attrs["llm.prompt_tokens"], 10)
        self.assertEqual(inference_attrs["llm.completion_tokens"], 5)
        self.assertEqual(inference_attrs["llm.total_tokens"], 15)

    def test_metrics_agent_span_and_timer_cover_post_price_calculations(self):
        events = []

        class Price:
            def get_history(self, ticker, lookback_days=365):
                del ticker, lookback_days
                events.append("price-history")
                return {
                    "ticker": "SLOW",
                    "dates": [],
                    "closes": [100.0, 101.0, 102.0, 103.0],
                    "source": "test",
                }

        @contextmanager
        def fake_span(name, attributes=None):
            del attributes
            events.append(f"span-enter:{name}")
            yield object()
            events.append(f"span-exit:{name}")

        @contextmanager
        def fake_agent_timer(agent):
            events.append(f"timer-enter:{agent}")
            yield
            events.append(f"timer-exit:{agent}")

        agent = MetricsAgent(price_agent=StaticPriceAgent())
        original = agent._max_drawdown

        def slow_drawdown(closes):
            events.append("drawdown")
            return original(closes)

        agent._max_drawdown = slow_drawdown
        agent.price = Price()

        with patch(
            "portfolio.portfolio.agents.metrics_agent.start_as_current_span",
            side_effect=fake_span,
        ), patch(
            "portfolio.portfolio.agents.metrics_agent.portfolio_metrics.agent_timer",
            side_effect=fake_agent_timer,
        ):
            result = agent.compute("SLOW", 30)

        self.assertEqual(result["ticker"], "SLOW")
        self.assertLess(events.index("span-enter:MetricsAgent[SLOW]"), events.index("price-history"))
        self.assertLess(events.index("price-history"), events.index("drawdown"))
        self.assertLess(events.index("drawdown"), events.index("span-exit:MetricsAgent[SLOW]"))
        self.assertLess(events.index("drawdown"), events.index("timer-exit:MetricsAgent"))

    async def test_telemetry_export_failure_does_not_fail_workflow_request(self):
        configure_tracing(
            ObservabilityConfig(
                service_name="bad-exporter",
                otlp_endpoint="http://127.0.0.1:1",
                tracing_sample_ratio=1.0,
            )
        )
        gateway_app = self._app()

        async with _lifespan_client(gateway_app) as gateway_client:
            response = await _post_gateway(gateway_client)

        self.assertEqual(response.status_code, 200)

    async def test_sampling_configuration_respected(self):
        before = len(get_finished_spans())
        configure_tracing(
            ObservabilityConfig(service_name="phase6-test", tracing_sample_ratio=0.0)
        )
        with start_as_current_span("unsampled-span"):
            pass
        configure_tracing(
            ObservabilityConfig(service_name="phase6-test", tracing_sample_ratio=1.0)
        )

        self.assertEqual(len(get_finished_spans()), before)

    def test_observability_config_files_validate_syntactically(self):
        for path in (ROOT / "observability" / "grafana" / "dashboards").glob("*.json"):
            with self.subTest(path=path):
                json.loads(path.read_text())

        yaml_like_files = [
            ROOT / "observability" / "prometheus" / "prometheus.yml",
            ROOT / "observability" / "prometheus" / "rules" / "alerts.yml",
            ROOT / "observability" / "alertmanager" / "alertmanager.yml",
            ROOT / "observability" / "otel" / "collector.yaml",
            ROOT / "observability" / "loki" / "loki.yaml",
            ROOT / "observability" / "grafana" / "provisioning" / "datasources" / "datasources.yml",
            ROOT / "observability" / "grafana" / "provisioning" / "dashboards" / "dashboards.yml",
        ]
        for path in yaml_like_files:
            with self.subTest(path=path):
                text = path.read_text()
                self.assertNotIn("\t", text)
                self.assertRegex(text, r":")

        alloy = (ROOT / "observability" / "alloy" / "config.alloy").read_text()
        self.assertIn("loki.source.docker", alloy)
        self.assertIn("loki.write", alloy)

    def _app(self):
        self.runtime = _runtime()
        gateway_app = create_gateway_app(
            config=GatewayConfig(max_in_flight=2, queue_capacity=2),
            client_factory=lambda _config: RuntimePortfolioClient(self.runtime),
            observability_config=ObservabilityConfig(
                service_name="gateway-test",
                json_logging=False,
            ),
        )
        return gateway_app


def _runtime():
    transport = httpx.MockTransport(lambda request: _completion())
    client = OpenAICompatibleInferenceClient(
        InferenceClientConfig(base_url="http://vllm:8000/v1"),
        transport=transport,
    )
    return PortfolioRuntime(
        config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
        metrics_agent=MetricsAgent(price_agent=StaticPriceAgent()),
        risk_agent=RiskAgent(),
        advisor=VLLMAdvisorAgent(client=client),
    )


def _completion():
    return httpx.Response(
        200,
        json={
            "model": "Qwen/Qwen3-4B-Instruct-2507",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Observed summary.",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


async def _post_gateway(client):
    return await client.post(
        "/v1/analyze",
        headers={
            "X-Run-ID": "run-obs",
            "X-Request-ID": "request-obs",
            "X-Query-ID": "query-obs",
        },
        json={"holdings": {"AAPL": 1.0}, "lookback_days": 30},
    )


@asynccontextmanager
async def _lifespan_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


def portfolio_metrics_text():
    from portfolio.portfolio.observability import render_portfolio_metrics

    return render_portfolio_metrics().decode()




if __name__ == "__main__":
    unittest.main()
