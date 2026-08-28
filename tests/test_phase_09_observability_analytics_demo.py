import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from portfolio.portfolio.agents.metrics_agent import MetricsAgent
from portfolio.portfolio.agents.risk_agent import RiskAgent
from portfolio.portfolio.agents.price_agent import PriceAgent
from portfolio.portfolio.analytics import (
    AnalyticsDataset,
    CostProfile,
    ExecutionObservation,
    ExperimentRun,
    InferenceObservationRecord,
    RequestObservation,
    ResourceSample,
)
from portfolio.portfolio.analytics.calculators import calculate_costs
from portfolio.portfolio.analytics.exporters import (
    generate_visual_report,
    write_parquet_run_artifacts,
)
from portfolio.portfolio.analytics.serving import summarize_serving_telemetry
from portfolio.portfolio.observability import (
    ObservabilityConfig,
    configure_tracing,
    get_finished_spans,
    reset_observability_for_tests,
)
from portfolio.portfolio.service import PortfolioRuntime, RequestContext, WorkflowRuntimeConfig


ROOT = Path(__file__).resolve().parents[1]


class Phase9ObservabilityAnalyticsDemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_agent_spans_carry_request_identity_for_historical_export(self):
        reset_observability_for_tests()
        configure_tracing(
            ObservabilityConfig(service_name="phase9-test", tracing_sample_ratio=1.0),
            in_memory=True,
        )
        runtime = PortfolioRuntime(
            config=WorkflowRuntimeConfig(cpu_workers=2, max_concurrent_metric_tasks=2),
            metrics_agent=MetricsAgent(price_agent=StaticPriceAgent()),
            risk_agent=RiskAgent(),
            advisor=StaticAdvisor(),
        )
        try:
            await runtime.analyze(
                holdings={"AAPL": 1.0},
                lookback_days=5,
                context=RequestContext(
                    run_id="run-phase9",
                    request_id="request-phase9",
                    query_id="query-phase9",
                ),
            )
        finally:
            await runtime.close()

        spans = {
            span.name: span.attributes
            for span in get_finished_spans()
            if span.name
            in {
                "MetricsAgent[AAPL]",
                "PriceAgent.get_history[AAPL]",
                "RiskAgent.assess",
            }
        }
        self.assertEqual(spans["MetricsAgent[AAPL]"]["request_id"], "request-phase9")
        self.assertEqual(
            spans["PriceAgent.get_history[AAPL]"]["query_id"],
            "query-phase9",
        )
        self.assertEqual(spans["RiskAgent.assess"]["run_id"], "run-phase9")

    async def test_visual_report_generation_from_persisted_artifacts(self):
        with TemporaryDirectory() as tempdir:
            output_dir = _write_fixture_run(Path(tempdir))

            index = generate_visual_report(output_dir)
            html = index.read_text()
            assets = output_dir / "report" / "assets"
            manifest = json.loads((assets / "chart_manifest.json").read_text())

        self.assertIn("Assignment Metrics", html)
        self.assertIn("Inference &amp; Serving", html)
        self.assertIn("Request Drilldown", html)
        self.assertIn("NONCANONICAL / ILLUSTRATIVE COST ONLY", html)
        self.assertIn("overhead_unallocated", html)
        self.assertIn("request-ok", html)
        self.assertNotIn("NaN", html)
        self.assertNotIn("Infinity", html)
        self.assertIn("cost_query_histogram", manifest)
        self.assertIn("agent_cost_share", manifest)
        self.assertGreater(len(manifest), 10)

    async def test_missing_gpu_telemetry_renders_as_unavailable_not_zero(self):
        summary = summarize_serving_telemetry(
            resource_samples=[],
            inference_observations=[
                {
                    "elapsed_ms": 125.0,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
            ],
        )

        self.assertEqual(summary["gpu"]["utilization_percent"]["availability"], "unavailable")
        self.assertIsNone(summary["gpu"]["utilization_percent"]["max"])
        self.assertEqual(summary["vllm"]["vllm:ttft_p95"]["availability"], "unavailable")
        self.assertEqual(summary["inference"]["total_tokens"], 15)

    async def test_visual_report_serving_telemetry_interprets_prometheus_aggregates(self):
        summary = summarize_serving_telemetry(
            resource_samples=[
                ResourceSample(
                    run_id="run-phase9",
                    timestamp="2026-08-27T00:00:01+00:00",
                    resource_type="prometheus",
                    resource_id="vllm:ttft_p95",
                    raw={
                        "metric": {
                            "name": "vllm:ttft_p95",
                            "availability": "available",
                        },
                        "value": 0.12,
                    },
                )
            ],
            inference_observations=[],
        )

        self.assertEqual(summary["vllm"]["vllm:ttft_p95"]["availability"], "available")
        self.assertAlmostEqual(summary["vllm"]["vllm:ttft_p95"]["latest"], 120.0)
        self.assertIn("aggregate run-level", summary["source_note"])

    async def test_grafana_historical_dashboard_parses_and_uses_schema_fields(self):
        dashboard = json.loads(
            (ROOT / "observability/grafana/dashboards/historical_experiments.json").read_text()
        )
        schema = (ROOT / "portfolio/portfolio/analytics/schema.sql").read_text()
        queries = "\n".join(
            target["rawSql"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
            if "rawSql" in target
        )
        variables = {item["name"] for item in dashboard["templating"]["list"]}

        self.assertEqual({"run_id", "profile_id", "request_id"}, variables)
        for table in (
            "experiment_runs",
            "requests",
            "execution_observations",
            "inference_observations",
            "resource_samples",
            "cost_analyses",
            "request_cost_attributions",
            "agent_cost_breakdown",
        ):
            self.assertIn(table, schema + queries)
        self.assertIn("overhead_unallocated", json.dumps(dashboard))
        self.assertIn("raw->>'trace_id'", queries)

    async def test_loki_datasource_links_trace_ids_to_jaeger(self):
        datasources = (
            ROOT / "observability/grafana/provisioning/datasources/datasources.yml"
        ).read_text()

        self.assertIn("uid: jaeger", datasources)
        self.assertIn("derivedFields", datasources)
        self.assertIn("trace_id", datasources)


class StaticPriceAgent(PriceAgent):
    def __init__(self):
        super().__init__(use_yfinance=False)

    def get_history(self, ticker: str, lookback_days: int = 365) -> dict:
        return {
            "ticker": ticker,
            "dates": [],
            "closes": [100.0, 101.0, 102.0, 103.0, 104.0],
            "source": "test",
        }


class StaticAdvisor:
    async def summarize_async(self, holdings, metrics, risk, context=None):
        return "Static summary."

    async def aclose(self):
        return None


def _write_fixture_run(root: Path) -> Path:
    dataset = _dataset()
    profile = CostProfile(
        name="reference_cpu_demo",
        version="1",
        machine_hourly_usd=360.0,
        cpu_pool_fraction=0.30,
        gpu_pool_fraction=0.50,
        overhead_pool_fraction=0.20,
        notes="NONCANONICAL SYNTHETIC illustrative local CPU cost profile",
    )
    analysis = calculate_costs(dataset, profile)
    output_dir = write_parquet_run_artifacts(
        output_dir=root / "analytics" / dataset.run.run_id,
        dataset=dataset,
        analysis=analysis,
    )
    (output_dir / "provenance.json").write_text(
        json.dumps(
            {
                "git_commit": "fixture-sha",
                "cost_profile": {
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "version": profile.version,
                    "machine_hourly_usd": profile.machine_hourly_usd,
                    "notes": profile.notes,
                },
                "inference_profile": {
                    "resolved": {
                        "model": "Qwen/Qwen3-0.6B",
                        "vllm_version": "0.8.5",
                        "dtype": "bfloat16",
                        "max_model_len": 4096,
                        "max_num_seqs": 8,
                        "prefix_caching_enabled": False,
                    }
                },
                "host": {"cpu": {"logical_count": 8}},
            },
            indent=2,
        )
        + "\n"
    )
    return output_dir


def _dataset() -> AnalyticsDataset:
    return AnalyticsDataset(
        run=ExperimentRun(
            run_id="run-phase9-fixture",
            started_at="2026-08-27T00:00:00+00:00",
            finished_at="2026-08-27T00:00:10+00:00",
            dataset_mode="sampled_2",
            selected_query_count=2,
            benchmark_concurrency=2,
            model="Qwen/Qwen3-0.6B",
            vllm_version="0.8.5",
            dtype="bfloat16",
            max_model_len=4096,
            max_num_seqs=8,
            prefix_caching_enabled=False,
        ),
        requests=(
            RequestObservation(
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                n_holdings=2,
                phrasing="percent",
                lookback_days=90,
                start_timestamp="2026-08-27T00:00:00+00:00",
                finish_timestamp="2026-08-27T00:00:04+00:00",
                client_latency_ms=4000.0,
                http_status=200,
                success=True,
            ),
            RequestObservation(
                run_id="run-phase9-fixture",
                request_id="request-fail",
                query_id="query-fail",
                n_holdings=1,
                phrasing="equal",
                lookback_days=90,
                start_timestamp="2026-08-27T00:00:04+00:00",
                finish_timestamp="2026-08-27T00:00:10+00:00",
                client_latency_ms=6000.0,
                http_status=503,
                success=False,
                error_type="saturation",
            ),
        ),
        execution_observations=(
            ExecutionObservation(
                observation_id="portfolio",
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                stage="portfolio",
                agent="PortfolioRuntime",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:04+00:00",
                wall_time_ms=4000.0,
            ),
            ExecutionObservation(
                observation_id="metrics-aapl",
                parent_observation_id="portfolio",
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                stage="metrics",
                agent="MetricsAgent",
                tool="compute",
                ticker="AAPL",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=100.0,
            ),
            ExecutionObservation(
                observation_id="price-aapl",
                parent_observation_id="metrics-aapl",
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                stage="metrics",
                agent="PriceAgent",
                tool="get_history",
                ticker="AAPL",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=50.0,
            ),
            ExecutionObservation(
                observation_id="risk",
                parent_observation_id="portfolio",
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                stage="risk",
                agent="RiskAgent",
                tool="assess",
                started_at="2026-08-27T00:00:01+00:00",
                finished_at="2026-08-27T00:00:02+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=100.0,
            ),
            ExecutionObservation(
                observation_id="advisor",
                parent_observation_id="portfolio",
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                stage="advisor",
                agent="AdvisorAgent",
                tool="summarize",
                started_at="2026-08-27T00:00:02+00:00",
                finished_at="2026-08-27T00:00:04+00:00",
                wall_time_ms=2000.0,
                cpu_time_ms=10.0,
            ),
        ),
        inference_observations=(
            InferenceObservationRecord(
                run_id="run-phase9-fixture",
                request_id="request-ok",
                query_id="query-ok",
                model="Qwen/Qwen3-0.6B",
                started_at="2026-08-27T00:00:02+00:00",
                finished_at="2026-08-27T00:00:04+00:00",
                elapsed_ms=2000.0,
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                status=200,
                raw={"trace_id": "trace-fixture", "span_id": "span-fixture"},
            ),
        ),
        resource_samples=(
            ResourceSample(
                run_id="run-phase9-fixture",
                timestamp="2026-08-27T00:00:02+00:00",
                resource_type="prometheus",
                resource_id="vllm",
                raw={
                    "source": "prometheus_range_query",
                    "metric": {
                        "name": "vllm:num_requests_running",
                        "availability": "available",
                    },
                    "value": 1.0,
                },
            ),
            ResourceSample(
                run_id="run-phase9-fixture",
                timestamp="2026-08-27T00:00:03+00:00",
                resource_type="prometheus",
                resource_id="vllm",
                raw={
                    "source": "prometheus_range_query",
                    "metric": {
                        "name": "vllm:ttft_p95",
                        "availability": "available",
                    },
                    "value": 0.22,
                },
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
