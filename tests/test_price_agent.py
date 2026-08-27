import unittest
from unittest.mock import patch

from portfolio.portfolio.agents.price_agent import PriceAgent

from tests.helpers import import_without_yfinance


class PriceAgentTests(unittest.TestCase):
    def test_synthetic_result_contract_and_determinism(self):
        agent = PriceAgent(use_yfinance=False)

        first = agent.get_history("AAPL", 10)
        second = agent.get_history("AAPL", 10)
        other = agent.get_history("MSFT", 10)

        self.assertEqual(first["ticker"], "AAPL")
        self.assertEqual(first["source"], "synthetic")
        self.assertEqual(first["dates"], [])
        self.assertEqual(len(first["closes"]), 10)
        self.assertEqual(first["closes"], second["closes"])
        self.assertNotEqual(first["closes"], other["closes"])

    def test_external_retrieval_failure_falls_back_to_synthetic(self):
        agent = PriceAgent(use_yfinance=True)

        with patch("builtins.__import__", side_effect=import_without_yfinance):
            result = agent.get_history("AAPL", 5)

        self.assertEqual(result["source"], "synthetic")
        self.assertEqual(len(result["closes"]), 5)


if __name__ == "__main__":
    unittest.main()
