import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from portfolio.portfolio.analytics import (
    AnalyticsDataset,
    CostProfile,
    ExecutionObservation,
    ExperimentRun,
    InferenceObservationRecord,
    RequestObservation,
    ResourceSample,
    default_metric_registry,
    load_cost_profile,
)
from portfolio.portfolio.analytics.calculators import (
    agent_tool_latency_summary,
    calculate_costs,
    fanout_work_summary,
    recalculate_costs,
    total_run_cost_usd,
)
from portfolio.portfolio.analytics.exporters import (
    PostgresAnalyticsRepository,
    build_report_from_artifacts,
    load_schema_sql,
    read_parquet_rows,
    write_parquet_run_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


class Phase7AnalyticsCostTests(unittest.TestCase):
    def test_raw_observations_persist_and_load_correctly(self):
        dataset = _dataset()
        connection = RecordingConnection()
        repository = PostgresAnalyticsRepository(connection)

        repository.persist_raw_dataset(dataset)

        sql_text = "\n".join(statement for statement, _params in connection.statements)
        self.assertIn("INSERT INTO experiment_runs", sql_text)
        self.assertIn("INSERT INTO requests", sql_text)
        self.assertIn("INSERT INTO execution_observations", sql_text)
        self.assertIn("INSERT INTO inference_observations", sql_text)
        self.assertIn("INSERT INTO resource_samples", sql_text)
        self.assertTrue(connection.committed)
        self.assertTrue(any(params and params[1] == "req-fail" for _sql, params in connection.statements))

    def test_run_request_execution_hierarchy_relationships_preserved(self):
        dataset = _dataset()
        by_id = {
            item.observation_id: item
            for item in dataset.execution_observations
            if item.observation_id
        }

        self.assertEqual(
            by_id["price-aapl"].parent_observation_id,
            "metrics-aapl",
        )
        self.assertEqual(
            by_id["metrics-aapl"].parent_observation_id,
            "metrics-stage",
        )
        self.assertEqual(by_id["risk"].parent_observation_id, "portfolio")

    def test_failed_requests_remain_stored_and_attributed(self):
        dataset = _dataset()
        analysis = calculate_costs(dataset, _profile())
        failed = [row for row in analysis.request_costs if not row.success]

        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].request_id, "req-fail")
        self.assertGreater(failed[0].total_cost_usd, 0.0)

    def test_postgres_and_parquet_required_fields_agree_round_trip(self):
        dataset = _dataset()
        analysis = calculate_costs(dataset, _profile())

        with TemporaryDirectory() as tempdir:
            output_dir = write_parquet_run_artifacts(
                output_dir=tempdir,
                dataset=dataset,
                analysis=analysis,
            )
            request_rows = read_parquet_rows(Path(output_dir) / "requests.parquet")
            inference_rows = read_parquet_rows(
                Path(output_dir) / "inference_observations.parquet"
            )
            run_json = json.loads((Path(output_dir) / "run.json").read_text())

        schema = load_schema_sql()
        for table_name in [
            "experiment_runs",
            "requests",
            "execution_observations",
            "inference_observations",
            "resource_samples",
            "cost_profiles",
            "metric_registry",
            "request_cost_attributions",
            "agent_cost_attributions",
        ]:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", schema)

        self.assertEqual(run_json["run_id"], dataset.run.run_id)
        self.assertEqual({row["request_id"] for row in request_rows}, {"req-ok", "req-fail"})
        self.assertEqual(inference_rows[0]["prompt_tokens"], 100)
        self.assertIn("raw JSONB", schema)
        self.assertIn("profile_id TEXT NOT NULL REFERENCES cost_profiles", schema)
        self.assertIn("UNIQUE(run_id, profile_id, request_id)", schema)

    def test_total_run_cost_formula_correct(self):
        dataset = _dataset()

        self.assertAlmostEqual(total_run_cost_usd(dataset, _profile()), 1.0)

    def test_attributed_request_costs_sum_to_total_pool(self):
        dataset = _dataset()
        analysis = calculate_costs(dataset, _profile())

        self.assertAlmostEqual(
            sum(row.total_cost_usd for row in analysis.request_costs),
            analysis.total_run_cost_usd,
            places=9,
        )

    def test_agent_cost_shares_sum_and_include_required_agents(self):
        analysis = calculate_costs(_dataset(), _profile())
        agents = {row.agent for row in analysis.agent_costs}

        self.assertTrue(
            {"PriceAgent", "MetricsAgent", "RiskAgent", "AdvisorAgent"}.issubset(agents)
        )
        self.assertAlmostEqual(
            sum(row.cost_percentage for row in analysis.agent_costs),
            100.0,
            places=9,
        )
        overhead = next(row for row in analysis.agent_costs if row.agent == "overhead_unallocated")
        advisor = next(row for row in analysis.agent_costs if row.agent == "AdvisorAgent")
        metrics = next(row for row in analysis.agent_costs if row.agent == "MetricsAgent")
        self.assertAlmostEqual(overhead.cost_percentage, 20.0)
        self.assertAlmostEqual(advisor.cost_percentage, 50.0)
        self.assertEqual(metrics.raw["boundary"], "non-overlapping configured pools")

    def test_concurrent_gpu_attribution_does_not_double_count_overlapping_wall_time(self):
        dataset = _dataset_with_two_overlapping_successful_inferences()
        analysis = calculate_costs(dataset, _profile())
        naive_wall_time_cost = 2 * (10.0 / 3600.0) * _profile().machine_hourly_usd

        self.assertAlmostEqual(analysis.total_run_cost_usd, 1.0)
        self.assertAlmostEqual(sum(row.gpu_cost_usd for row in analysis.request_costs), 0.5)
        self.assertGreater(naive_wall_time_cost, analysis.total_run_cost_usd)
        self.assertAlmostEqual(analysis.request_cost_sum_usd, analysis.total_run_cost_usd)

    def test_changing_cost_profile_recalculates_without_mutating_raw_observations(self):
        dataset = _dataset()
        raw_before = [request.raw.copy() for request in dataset.requests]

        first = recalculate_costs(dataset, _profile())
        second = recalculate_costs(
            dataset,
            CostProfile(
                name="fixture",
                version="double",
                machine_hourly_usd=720.0,
                cpu_pool_fraction=0.30,
                gpu_pool_fraction=0.50,
                overhead_pool_fraction=0.20,
                decode_token_weight=8.0,
            ),
        )

        self.assertAlmostEqual(second.total_run_cost_usd, first.total_run_cost_usd * 2)
        self.assertEqual([request.raw for request in dataset.requests], raw_before)

    def test_cost_query_percentiles_correct(self):
        values = [0.10, 0.20, 0.30, 0.40]
        dataset = _dataset_for_known_costs(values)
        analysis = calculate_costs(dataset, _profile_for_exact_total(sum(values)))
        metric = _metric_value(analysis, "cost_per_query_distribution_usd")

        self.assertAlmostEqual(metric["min"], 0.10)
        self.assertAlmostEqual(metric["max"], 0.40)
        self.assertAlmostEqual(metric["p50"], 0.25)
        self.assertAlmostEqual(metric["p75"], 0.325)

    def test_agent_tool_latency_and_cost_aggregations_correct(self):
        dataset = _dataset()
        latency_rows = agent_tool_latency_summary(dataset)
        price_row = next(row for row in latency_rows if row["agent"] == "PriceAgent")
        analysis = calculate_costs(dataset, _profile())
        price_cost = next(row for row in analysis.agent_costs if row.agent == "PriceAgent")

        self.assertEqual(price_row["calls"], 3)
        self.assertEqual(price_row["failures"], 1)
        self.assertGreater(price_cost.attributed_cost_usd, 0.0)

    def test_cumulative_fanout_work_distinguished_from_critical_path_latency(self):
        summary = fanout_work_summary(_dataset())
        metrics_row = next(
            row
            for row in summary
            if row["stage"] == "metrics" and row["request_id"] == "req-ok"
        )

        self.assertGreater(
            metrics_row["cumulative_wall_time_ms"],
            metrics_row["critical_path_wall_time_ms"],
        )

    def test_generated_reports_and_charts_read_persisted_artifacts(self):
        dataset = _dataset()
        analysis = calculate_costs(dataset, _profile())

        with TemporaryDirectory() as tempdir:
            output_dir = write_parquet_run_artifacts(
                output_dir=tempdir,
                dataset=dataset,
                analysis=analysis,
            )
            report = build_report_from_artifacts(output_dir)
            charts_dir = Path(output_dir) / "charts"
            chart_files = {path.name for path in charts_dir.iterdir()}

        self.assertEqual(report["request_count"], 2)
        self.assertAlmostEqual(report["total_run_cost_usd"], analysis.total_run_cost_usd)
        self.assertTrue(
            {
                "cost_query_histogram.json",
                "cost_query_boxplot.json",
                "cost_query_cdf.json",
                "latency_distribution.json",
                "cost_latency_by_holdings.json",
                "cost_vs_tokens.json",
                "agent_cost_share.json",
                "query_type_breakdown.json",
                "holdings_count_breakdown.json",
            }.issubset(chart_files)
        )
        percent_group = next(
            row for row in report["query_type_breakdown"] if row["group"] == "percent"
        )
        equal_group = next(
            row for row in report["query_type_breakdown"] if row["group"] == "equal"
        )
        self.assertEqual(percent_group["count"], 1)
        self.assertEqual(percent_group["success_rate"], 1.0)
        self.assertEqual(percent_group["average_prompt_tokens"], 100.0)
        self.assertEqual(percent_group["average_completion_tokens"], 25.0)
        self.assertEqual(equal_group["success_rate"], 0.0)

    def test_different_cost_profiles_persist_without_ambiguity(self):
        dataset = _dataset()
        first = calculate_costs(dataset, _profile())
        second = calculate_costs(
            dataset,
            CostProfile(
                name="fixture",
                version="alt",
                machine_hourly_usd=720.0,
                cpu_pool_fraction=0.40,
                gpu_pool_fraction=0.40,
                overhead_pool_fraction=0.20,
            ),
        )
        connection = RecordingConnection()
        repository = PostgresAnalyticsRepository(connection)

        repository.persist_cost_analysis(first)
        repository.persist_cost_analysis(second)

        sql_text = "\n".join(statement for statement, _params in connection.statements)
        request_profiles = [
            params[1]
            for statement, params in connection.statements
            if "INSERT INTO request_cost_attributions" in statement
        ]
        agent_profiles = [
            params[1]
            for statement, params in connection.statements
            if "INSERT INTO agent_cost_attributions" in statement
        ]
        metric_profiles = [
            params[1]
            for statement, params in connection.statements
            if "INSERT INTO derived_metrics" in statement
        ]

        self.assertIn("ON CONFLICT (run_id, profile_id)", sql_text)
        self.assertIn("ON CONFLICT (run_id, profile_id, request_id)", sql_text)
        self.assertIn("fixture:1", request_profiles)
        self.assertIn("fixture:alt", request_profiles)
        self.assertIn("fixture:1", agent_profiles)
        self.assertIn("fixture:alt", agent_profiles)
        self.assertIn("fixture:1", metric_profiles)
        self.assertIn("fixture:alt", metric_profiles)

    def test_metric_registry_and_reference_profile_are_versioned(self):
        registry = default_metric_registry()
        profile = load_cost_profile(ROOT / "configs" / "cost" / "reference_local.yaml")

        self.assertTrue(all(item.version for item in registry))
        self.assertIn(
            "total_infrastructure_cost_usd",
            {item.name for item in registry},
        )
        self.assertEqual(profile.name, "reference_local")
        self.assertEqual(profile.machine_hourly_usd, 0.0)


def _dataset() -> AnalyticsDataset:
    return AnalyticsDataset(
        run=ExperimentRun(
            run_id="run-phase7",
            started_at="2026-08-27T00:00:00+00:00",
            finished_at="2026-08-27T00:00:10+00:00",
            dataset_mode="fixture",
            selected_query_count=2,
            benchmark_concurrency=2,
            model="Qwen/Qwen3-4B-Instruct-2507",
        ),
        requests=(
            RequestObservation(
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                n_holdings=2,
                phrasing="percent",
                lookback_days=180,
                start_timestamp="2026-08-27T00:00:00+00:00",
                finish_timestamp="2026-08-27T00:00:10+00:00",
                client_latency_ms=10000.0,
                http_status=200,
                success=True,
                raw={"source": "benchmark"},
            ),
            RequestObservation(
                run_id="run-phase7",
                request_id="req-fail",
                query_id="q-fail",
                n_holdings=1,
                phrasing="equal",
                lookback_days=90,
                start_timestamp="2026-08-27T00:00:00+00:00",
                finish_timestamp="2026-08-27T00:00:05+00:00",
                client_latency_ms=5000.0,
                http_status=503,
                success=False,
                error_type="saturation",
                raw={"source": "benchmark"},
            ),
        ),
        execution_observations=(
            ExecutionObservation(
                observation_id="portfolio",
                parent_observation_id=None,
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="portfolio",
                agent="PortfolioRuntime",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:10+00:00",
                wall_time_ms=10000.0,
                cpu_time_ms=10.0,
            ),
            ExecutionObservation(
                observation_id="metrics-stage",
                parent_observation_id="portfolio",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="metrics",
                agent="MetricsAgent",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=20.0,
            ),
            ExecutionObservation(
                observation_id="metrics-aapl",
                parent_observation_id="metrics-stage",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="metrics",
                agent="MetricsAgent",
                tool="compute",
                ticker="AAPL",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=30.0,
            ),
            ExecutionObservation(
                observation_id="price-aapl",
                parent_observation_id="metrics-aapl",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="metrics",
                agent="PriceAgent",
                tool="get_history",
                ticker="AAPL",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=30.0,
            ),
            ExecutionObservation(
                observation_id="metrics-msft",
                parent_observation_id="metrics-stage",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="metrics",
                agent="MetricsAgent",
                tool="compute",
                ticker="MSFT",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=40.0,
            ),
            ExecutionObservation(
                observation_id="price-msft",
                parent_observation_id="metrics-msft",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="metrics",
                agent="PriceAgent",
                tool="get_history",
                ticker="MSFT",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:01+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=40.0,
            ),
            ExecutionObservation(
                observation_id="risk",
                parent_observation_id="portfolio",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="risk",
                agent="RiskAgent",
                tool="assess",
                started_at="2026-08-27T00:00:01+00:00",
                finished_at="2026-08-27T00:00:02+00:00",
                wall_time_ms=1000.0,
                cpu_time_ms=50.0,
            ),
            ExecutionObservation(
                observation_id="advisor",
                parent_observation_id="portfolio",
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                stage="advisor",
                agent="AdvisorAgent",
                tool="summarize",
                started_at="2026-08-27T00:00:02+00:00",
                finished_at="2026-08-27T00:00:10+00:00",
                wall_time_ms=8000.0,
                cpu_time_ms=10.0,
            ),
            ExecutionObservation(
                observation_id="failed-price",
                parent_observation_id=None,
                run_id="run-phase7",
                request_id="req-fail",
                query_id="q-fail",
                stage="metrics",
                agent="PriceAgent",
                tool="get_history",
                ticker="NVDA",
                started_at="2026-08-27T00:00:00+00:00",
                finished_at="2026-08-27T00:00:05+00:00",
                wall_time_ms=5000.0,
                cpu_time_ms=20.0,
                status="error",
                error_type="saturation",
            ),
        ),
        inference_observations=(
            InferenceObservationRecord(
                run_id="run-phase7",
                request_id="req-ok",
                query_id="q-ok",
                model="Qwen/Qwen3-4B-Instruct-2507",
                started_at="2026-08-27T00:00:02+00:00",
                finished_at="2026-08-27T00:00:10+00:00",
                elapsed_ms=8000.0,
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                status=200,
            ),
        ),
        resource_samples=(
            ResourceSample(
                run_id="run-phase7",
                timestamp="2026-08-27T00:00:05+00:00",
                resource_type="gpu",
                resource_id="gpu0",
                gpu_utilization=0.75,
                gpu_memory_used_bytes=1024,
                raw={"source": "dcgm"},
            ),
        ),
    )


def _dataset_with_two_overlapping_successful_inferences() -> AnalyticsDataset:
    base = _dataset()
    return AnalyticsDataset(
        run=base.run,
        requests=(
            base.requests[0],
            RequestObservation(
                run_id="run-phase7",
                request_id="req-ok-2",
                query_id="q-ok-2",
                n_holdings=2,
                phrasing="percent",
                lookback_days=180,
                start_timestamp="2026-08-27T00:00:00+00:00",
                finish_timestamp="2026-08-27T00:00:10+00:00",
                client_latency_ms=10000.0,
                http_status=200,
                success=True,
            ),
        ),
        execution_observations=base.execution_observations,
        inference_observations=(
            base.inference_observations[0],
            InferenceObservationRecord(
                run_id="run-phase7",
                request_id="req-ok-2",
                query_id="q-ok-2",
                model="Qwen/Qwen3-4B-Instruct-2507",
                elapsed_ms=10000.0,
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                status=200,
            ),
        ),
        resource_samples=base.resource_samples,
    )


def _dataset_for_known_costs(values):
    total = sum(values)
    duration_seconds = 10.0
    return AnalyticsDataset(
        run=ExperimentRun(
            run_id="run-percentiles",
            started_at="2026-08-27T00:00:00+00:00",
            finished_at="2026-08-27T00:00:10+00:00",
        ),
        requests=tuple(
            RequestObservation(
                run_id="run-percentiles",
                request_id=f"req-{index}",
                query_id=f"q-{index}",
                n_holdings=1,
                phrasing="equal",
                lookback_days=30,
                start_timestamp="2026-08-27T00:00:00+00:00",
                finish_timestamp="2026-08-27T00:00:10+00:00",
                client_latency_ms=duration_seconds * value / total * 1000.0,
                http_status=200,
                success=True,
            )
            for index, value in enumerate(values)
        ),
    )


def _profile() -> CostProfile:
    return CostProfile(
        name="fixture",
        version="1",
        machine_hourly_usd=360.0,
        cpu_pool_fraction=0.30,
        gpu_pool_fraction=0.50,
        overhead_pool_fraction=0.20,
    )


def _profile_for_exact_total(total_cost):
    return CostProfile(
        name="fixture",
        version="percentiles",
        machine_hourly_usd=total_cost * 360.0,
        cpu_pool_fraction=0.0,
        gpu_pool_fraction=0.0,
        overhead_pool_fraction=1.0,
    )


def _metric_value(analysis, name):
    return next(metric.value for metric in analysis.metrics if metric.name == name)


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.committed = False

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        self.committed = True


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def execute(self, statement, params=None):
        self.connection.statements.append((statement, params))


if __name__ == "__main__":
    unittest.main()
