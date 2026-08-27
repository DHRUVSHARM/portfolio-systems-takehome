import unittest
from unittest.mock import patch

from portfolio.portfolio.agents.advisor_agent import AdvisorAgent

from tests.helpers import import_without_boto3


class AdvisorAgentTests(unittest.TestCase):
    def setUp(self):
        self.holdings = {"AAPL": 0.6, "MSFT": 0.4}
        self.metrics = {
            "AAPL": {
                "annualized_return": 0.18,
                "annualized_volatility": 0.25,
                "sharpe": 0.72,
                "max_drawdown": -0.15,
            },
            "MSFT": {
                "annualized_return": 0.14,
                "annualized_volatility": 0.22,
                "sharpe": 0.64,
                "max_drawdown": -0.12,
            },
        }
        self.risk = {
            "portfolio_annualized_return": 0.164,
            "portfolio_annualized_volatility": 0.21,
            "portfolio_sharpe": 0.78,
            "concentration_hhi": 0.52,
            "diversification_ratio": 1.12,
            "top_holding": {"ticker": "AAPL", "weight": 0.6},
        }

    def test_build_prompt_contains_grounded_figures(self):
        prompt = AdvisorAgent()._build_prompt(self.holdings, self.metrics, self.risk)

        self.assertIn("portfolio analyst", prompt)
        self.assertIn("AAPL", prompt)
        self.assertIn("ann_return=18.0%", prompt)
        self.assertIn("HHI=0.52", prompt)

    def test_summarize_completes_without_bedrock(self):
        agent = AdvisorAgent()

        with patch("builtins.__import__", side_effect=import_without_boto3):
            summary = agent.summarize(self.holdings, self.metrics, self.risk)

        self.assertIsInstance(summary, str)
        self.assertGreater(len(summary), 0)
        self.assertIn("annualized return", summary)


if __name__ == "__main__":
    unittest.main()
