import unittest

from portfolio.portfolio.agents.risk_agent import RiskAgent


class RiskAgentTests(unittest.TestCase):
    def test_portfolio_risk_metrics_are_produced(self):
        metrics = {
            "AAPL": {
                "annualized_return": 0.18,
                "annualized_volatility": 0.25,
                "returns": [0.01, -0.02, 0.015, 0.0, -0.01],
            },
            "MSFT": {
                "annualized_return": 0.14,
                "annualized_volatility": 0.22,
                "returns": [0.005, -0.01, 0.02, -0.005, 0.01],
            },
        }

        result = RiskAgent().assess({"AAPL": 60, "MSFT": 40}, metrics)

        self.assertEqual(result["n_holdings"], 2)
        self.assertEqual(result["weights"], {"AAPL": 0.6, "MSFT": 0.4})
        self.assertAlmostEqual(result["portfolio_annualized_return"], 0.164)
        self.assertIn("portfolio_annualized_volatility", result)
        self.assertIn("portfolio_sharpe", result)
        self.assertEqual(result["concentration_hhi"], 0.52)
        self.assertIn("diversification_ratio", result)
        self.assertEqual(result["top_holding"], {"ticker": "AAPL", "weight": 0.6})

    def test_invalid_metrics_are_excluded_and_weights_renormalized(self):
        metrics = {
            "AAPL": {
                "annualized_return": 0.18,
                "annualized_volatility": 0.25,
                "returns": [0.01, -0.02, 0.015],
            },
            "BAD": {"ticker": "BAD", "error": "insufficient price data"},
        }

        result = RiskAgent().assess({"AAPL": 0.5, "BAD": 0.5}, metrics)

        self.assertEqual(result["n_holdings"], 1)
        self.assertEqual(result["weights"], {"AAPL": 1.0})

    def test_insufficient_covariance_history_uses_weighted_vol_fallback(self):
        metrics = {
            "AAPL": {
                "annualized_return": 0.10,
                "annualized_volatility": 0.20,
                "returns": [0.01],
            },
            "MSFT": {
                "annualized_return": 0.20,
                "annualized_volatility": 0.30,
                "returns": [0.02],
            },
        }

        result = RiskAgent().assess({"AAPL": 0.25, "MSFT": 0.75}, metrics)

        self.assertEqual(result["portfolio_annualized_volatility"], 0.275)


if __name__ == "__main__":
    unittest.main()
