import json
import unittest
from pathlib import Path

from portfolio.portfolio.benchmark_adapter import (
    QueryAdapterError,
    normalize_query_record,
    normalize_query_records,
)


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "queries.json").open() as file:
            cls.records = json.load(file)
        cls.by_id = {record["id"]: record for record in cls.records}

    def test_percent_record_with_mixed_names_and_tickers(self):
        normalized = normalize_query_record(self.by_id[1])

        self.assertEqual(normalized.lookback_days, 545)
        self.assertEqual(
            normalized.holdings,
            {"AAPL": 0.14, "UNH": 0.73, "AMD": 0.08, "NFLX": 0.05},
        )

    def test_equal_record_produces_equal_weights(self):
        normalized = normalize_query_record(self.by_id[6])

        self.assertEqual(normalized.holdings, {"COST": 0.5, "CSCO": 0.5})
        self.assertEqual(normalized.lookback_days, 180)

    def test_unweighted_record_and_null_lookback(self):
        normalized = normalize_query_record(self.by_id[9])

        self.assertEqual(
            set(normalized.holdings),
            {"ADBE", "AAPL", "JNJ", "UNH", "AMZN", "QCOM"},
        )
        self.assertAlmostEqual(sum(normalized.holdings.values()), 1.0)
        self.assertIsNone(normalized.lookback_days)
        self.assertNotIn("lookback_days", normalized.to_workflow_kwargs())

    def test_all_supplied_records_are_normalizable(self):
        normalized = normalize_query_records(self.records)

        self.assertEqual(len(normalized), 1000)

    def test_validation_rejects_bad_phrasing(self):
        record = dict(self.by_id[1])
        record["phrasing"] = "freeform"

        with self.assertRaises(QueryAdapterError):
            normalize_query_record(record)

    def test_validation_rejects_ticker_count_mismatch(self):
        record = dict(self.by_id[1])
        record["n_holdings"] = 5

        with self.assertRaises(QueryAdapterError):
            normalize_query_record(record)

    def test_validation_rejects_bad_lookback(self):
        record = dict(self.by_id[1])
        record["expected_lookback_days"] = 0

        with self.assertRaises(QueryAdapterError):
            normalize_query_record(record)


if __name__ == "__main__":
    unittest.main()
