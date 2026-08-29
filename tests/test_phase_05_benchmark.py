import asyncio
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from portfolio.portfolio.benchmark import (
    BenchmarkConfig,
    load_canonical_manifest,
    load_query_records,
    run_benchmark,
    select_query_records,
)
from portfolio.portfolio.benchmark.datasets import lookback_bucket
from portfolio.portfolio.benchmark_adapter import normalize_query_records


ROOT = Path(__file__).resolve().parents[1]


class Phase5BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_query_records(ROOT / "queries.json")
        cls.by_id = {record["id"]: record for record in cls.records}

    def test_all_1000_supplied_records_normalize_successfully(self):
        normalized = normalize_query_records(self.records)

        self.assertEqual(len(normalized), 1000)

    def test_canonical_manifest_has_exactly_100_unique_ids(self):
        manifest = load_canonical_manifest()
        query_ids = manifest["query_ids"]

        self.assertEqual(len(query_ids), 100)
        self.assertEqual(len(set(query_ids)), 100)
        self.assertTrue(all(query_id in self.by_id for query_id in query_ids))

    def test_canonical_selection_is_stable_and_reproducible(self):
        config = BenchmarkConfig(dataset_mode="canonical_100")

        first, first_meta = select_query_records(self.records, config)
        second, second_meta = select_query_records(self.records, config)

        self.assertEqual(
            [record["id"] for record in first],
            [record["id"] for record in second],
        )
        self.assertEqual(first_meta, second_meta)
        self.assertEqual(first_meta["selected_query_ids"], load_canonical_manifest()["query_ids"])

    def test_canonical_set_represents_holdings_phrasing_and_lookback_strata(self):
        selected, _metadata = select_query_records(
            self.records, BenchmarkConfig(dataset_mode="canonical_100")
        )

        holdings_counts = Counter(record["n_holdings"] for record in selected)
        phrasings = Counter(record["phrasing"] for record in selected)
        lookbacks = Counter(
            lookback_bucket(record["expected_lookback_days"]) for record in selected
        )
        strata = {
            (
                record["n_holdings"],
                record["phrasing"],
                lookback_bucket(record["expected_lookback_days"]),
            )
            for record in selected
        }

        self.assertEqual(set(holdings_counts), {1, 2, 3, 4, 5, 6})
        self.assertEqual(set(phrasings), {"percent", "equal", "unweighted"})
        self.assertEqual(set(lookbacks), {"<=90", "<=180", "<=365", ">365", "none"})
        self.assertGreaterEqual(len(strata), 80)

    def test_full_1000_selects_exactly_all_supplied_ids(self):
        selected, metadata = select_query_records(
            self.records, BenchmarkConfig(dataset_mode="full_1000")
        )

        self.assertEqual(len(selected), 1000)
        self.assertEqual(
            metadata["selected_query_ids"], [record["id"] for record in self.records]
        )

    def test_sampled_n_with_fixed_seed_is_deterministic(self):
        config = BenchmarkConfig(dataset_mode="sampled_N", sample_size=25, sample_seed=42)

        first, first_meta = select_query_records(self.records, config)
        second, second_meta = select_query_records(self.records, config)
        different, _metadata = select_query_records(
            self.records,
            BenchmarkConfig(dataset_mode="sampled_N", sample_size=25, sample_seed=43),
        )

        self.assertEqual([record["id"] for record in first], [record["id"] for record in second])
        self.assertEqual(first_meta, second_meta)
        self.assertNotEqual(
            [record["id"] for record in first],
            [record["id"] for record in different],
        )

    async def test_outstanding_http_requests_never_exceed_configured_concurrency(self):
        active = 0
        max_active = 0

        async def handler(request):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.02)
                return _gateway_response()
            finally:
                active -= 1

        await run_benchmark(
            BenchmarkConfig(
                dataset_mode="sampled_N",
                sample_size=20,
                sample_seed=1,
                concurrency=3,
                write_artifacts=False,
            ),
            records=self.records,
            transport=httpx.MockTransport(handler),
        )

        self.assertLessEqual(max_active, 3)

    async def test_unique_request_ids_generated_and_correlation_propagated(self):
        seen_headers = []

        def handler(request):
            seen_headers.append(dict(request.headers))
            return _gateway_response()

        result = await run_benchmark(
            BenchmarkConfig(
                dataset_mode="sampled_N",
                sample_size=5,
                sample_seed=4,
                concurrency=2,
                run_id="run-correlation",
                write_artifacts=False,
            ),
            records=self.records,
            transport=httpx.MockTransport(handler),
        )

        request_ids = [observation.request_id for observation in result.observations]
        self.assertEqual(len(request_ids), len(set(request_ids)))
        self.assertEqual([headers["x-run-id"] for headers in seen_headers], ["run-correlation"] * 5)
        self.assertEqual(
            [headers["x-request-id"] for headers in seen_headers],
            request_ids,
        )
        self.assertEqual(
            [headers["x-query-id"] for headers in seen_headers],
            [observation.query_id for observation in result.observations],
        )

    async def test_raw_success_latency_status_recorded(self):
        result = await run_benchmark(
            BenchmarkConfig(
                dataset_mode="sampled_N",
                sample_size=1,
                sample_seed=5,
                concurrency=1,
                run_id="run-success",
                write_artifacts=False,
            ),
            records=self.records,
            transport=httpx.MockTransport(lambda request: _gateway_response()),
        )

        observation = result.observations[0]
        self.assertTrue(observation.success)
        self.assertEqual(observation.http_status, 200)
        self.assertIsNone(observation.error_type)
        self.assertGreaterEqual(observation.client_latency_ms, 0.0)
        self.assertEqual(observation.run_id, "run-success")
        self.assertGreater(observation.lookback_days, 0)

    async def test_503_429_5xx_timeout_connection_and_malformed_are_distinguished(self):
        records = self.records[:6]

        def handler(request):
            query_id = request.headers["X-Query-ID"]
            index = [str(record["id"]) for record in records].index(query_id)
            if index == 0:
                return httpx.Response(503, json={"detail": "busy"})
            if index == 1:
                return httpx.Response(429, json={"detail": "rate limited"})
            if index == 2:
                return httpx.Response(500, json={"detail": "broken"})
            if index == 3:
                raise httpx.TimeoutException("slow", request=request)
            if index == 4:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, text="not json")

        result = await run_benchmark(
            BenchmarkConfig(
                dataset_mode="full_1000",
                concurrency=6,
                write_artifacts=False,
            ),
            records=records,
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(
            [observation.error_type for observation in result.observations],
            [
                "saturation",
                "rate_limit",
                "5xx",
                "timeout",
                "connection_failure",
                "malformed_response",
            ],
        )
        self.assertTrue(all(not observation.success for observation in result.observations))

    async def test_failed_requests_are_not_retried_and_remain_in_results(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, json={"detail": "busy"})

        result = await run_benchmark(
            BenchmarkConfig(
                dataset_mode="sampled_N",
                sample_size=4,
                sample_seed=7,
                concurrency=2,
                write_artifacts=False,
            ),
            records=self.records,
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(attempts, 4)
        self.assertEqual(len(result.observations), 4)
        self.assertEqual(result.run_metadata["failure_count"], 4)
        self.assertTrue(all(observation.error_type == "saturation" for observation in result.observations))

    async def test_e2e_runner_targets_gateway_not_internal_portfolio(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            return _gateway_response()

        await run_benchmark(
            BenchmarkConfig(
                dataset_mode="sampled_N",
                sample_size=3,
                sample_seed=8,
                concurrency=2,
                write_artifacts=False,
            ),
            records=self.records,
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(paths, ["/v1/analyze", "/v1/analyze", "/v1/analyze"])
        self.assertNotIn("/internal/analyze", paths)

    async def test_result_manifest_and_run_metadata_are_self_consistent(self):
        with TemporaryDirectory() as tempdir:
            result = await run_benchmark(
                BenchmarkConfig(
                    dataset_mode="sampled_N",
                    sample_size=3,
                    sample_seed=9,
                    concurrency=2,
                    run_id="run-artifacts",
                    output_root=tempdir,
                    write_artifacts=True,
                ),
                records=self.records,
                transport=httpx.MockTransport(lambda request: _gateway_response()),
            )

            output_dir = Path(result.output_dir)
            run_json = json.loads((output_dir / "run.json").read_text())
            config_text = (output_dir / "resolved_benchmark_config.yaml").read_text()
            request_rows = [
                json.loads(line)
                for line in (output_dir / "requests.jsonl").read_text().splitlines()
            ]

        self.assertEqual(output_dir.name, "run-artifacts")
        self.assertEqual(run_json["run_id"], "run-artifacts")
        self.assertEqual(run_json["request_count"], 3)
        self.assertEqual(run_json["success_count"], 3)
        self.assertEqual(run_json["failure_count"], 0)
        self.assertEqual(run_json["selected_query_count"], 3)
        self.assertEqual(len(run_json["selected_query_ids"]), 3)
        self.assertIn('dataset_mode: "sampled_N"', config_text)
        self.assertEqual(len(request_rows), 3)
        self.assertEqual(
            [row["request_id"] for row in request_rows],
            [observation.request_id for observation in result.observations],
        )


def _gateway_response():
    return httpx.Response(
        200,
        json={
            "holdings": {"AAPL": 1.0},
            "lookback_days": 30,
            "metrics": {"AAPL": {"ticker": "AAPL"}},
            "risk": {"portfolio_sharpe": 1.0},
            "summary": "ok",
        },
    )


if __name__ == "__main__":
    unittest.main()
