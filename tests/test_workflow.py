import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from portfolio.portfolio.agents.advisor_agent import AdvisorAgent
from portfolio.portfolio.agents.metrics_agent import MetricsAgent
from portfolio.portfolio.agents.price_agent import PriceAgent
from portfolio.portfolio.benchmark_adapter import normalize_query_record
from portfolio.portfolio.workflows.portfolio_workflow import main

from tests.helpers import import_without_boto3


ROOT = Path(__file__).resolve().parents[1]


class RecordingMetricsAgent:
    def __init__(self, events):
        self.events = events
        self.tools = [self.compute]

    def compute(self, ticker, lookback_days=365):
        self.events.append(("metric-start", ticker))
        time.sleep(0.05)
        self.events.append(("metric-end", ticker))
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


class RecordingRiskAgent:
    def __init__(self, events):
        self.events = events
        self.tools = [self.assess]

    def assess(self, holdings, metrics):
        self.events.append(("risk", tuple(metrics)))
        return {
            "n_holdings": len(metrics),
            "weights": holdings,
            "portfolio_annualized_return": 0.10,
            "portfolio_annualized_volatility": 0.20,
            "portfolio_sharpe": 0.5,
            "concentration_hhi": 0.34,
            "diversification_ratio": 1.0,
            "top_holding": {"ticker": next(iter(holdings)), "weight": next(iter(holdings.values()))},
        }


class RecordingAdvisorAgent:
    def summarize(self, holdings, metrics, risk):
        return "offline summary"


class WorkflowTests(unittest.TestCase):
    def test_local_fanout_reaches_barrier_before_risk_agent(self):
        events = []
        result = main(
            holdings={"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
            lookback_days=180,
            metrics_agent=RecordingMetricsAgent(events),
            risk_agent=RecordingRiskAgent(events),
            advisor=RecordingAdvisorAgent(),
        )

        first_end = next(i for i, event in enumerate(events) if event[0] == "metric-end")
        starts_before_first_end = [
            event for event in events[:first_end] if event[0] == "metric-start"
        ]
        risk_index = next(i for i, event in enumerate(events) if event[0] == "risk")
        end_indexes = [i for i, event in enumerate(events) if event[0] == "metric-end"]

        self.assertEqual(len(starts_before_first_end), 3)
        self.assertTrue(all(index < risk_index for index in end_indexes))
        self.assertEqual(set(result["metrics"]), {"AAPL", "MSFT", "NVDA"})
        self.assertNotIn("returns", result["metrics"]["AAPL"])

    def test_structured_workflow_offline_smoke(self):
        metrics_agent = MetricsAgent(price_agent=PriceAgent(use_yfinance=False))
        advisor = AdvisorAgent()

        with patch("builtins.__import__", side_effect=import_without_boto3):
            result = main(
                holdings={"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
                lookback_days=180,
                metrics_agent=metrics_agent,
                advisor=advisor,
            )

        self.assertEqual(result["holdings"], {"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25})
        self.assertEqual(result["lookback_days"], 180)
        self.assertEqual(set(result["metrics"]), {"AAPL", "MSFT", "NVDA"})
        self.assertIn("risk", result)
        self.assertIsInstance(result["summary"], str)
        self.assertGreater(len(result["summary"]), 0)
        self.assertTrue(all("returns" not in metric for metric in result["metrics"].values()))

    def test_real_query_records_to_workflow_offline_smoke(self):
        with (ROOT / "queries.json").open() as file:
            by_id = {record["id"]: record for record in json.load(file)}

        for record_id in (1, 4, 6, 9, 13):
            normalized = normalize_query_record(by_id[record_id])
            metrics_agent = MetricsAgent(price_agent=PriceAgent(use_yfinance=False))
            advisor = AdvisorAgent()

            with patch("builtins.__import__", side_effect=import_without_boto3):
                result = main(
                    **normalized.to_workflow_kwargs(),
                    metrics_agent=metrics_agent,
                    advisor=advisor,
                )

            self.assertEqual(len(result["holdings"]), by_id[record_id]["n_holdings"])
            self.assertAlmostEqual(sum(result["holdings"].values()), 1.0)
            if normalized.lookback_days is None:
                self.assertEqual(result["lookback_days"], 365)
            else:
                self.assertEqual(result["lookback_days"], normalized.lookback_days)
            self.assertEqual(set(result["metrics"]), set(result["holdings"]))
            self.assertIn("portfolio_annualized_return", result["risk"])
            self.assertGreater(len(result["summary"]), 0)
            self.assertTrue(all("returns" not in metric for metric in result["metrics"].values()))


if __name__ == "__main__":
    unittest.main()
