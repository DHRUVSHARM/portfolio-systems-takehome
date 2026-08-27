import unittest

from portfolio.portfolio.agents.metrics_agent import MetricsAgent


class FakePriceAgent:
    def __init__(self, closes):
        self.closes = closes

    def get_history(self, ticker, lookback_days=365):
        return {
            "ticker": ticker,
            "dates": [],
            "closes": self.closes,
            "source": "test",
        }


class MetricsAgentTests(unittest.TestCase):
    def test_metrics_accept_native_price_dict_and_keep_returns(self):
        agent = MetricsAgent(price_agent=FakePriceAgent([100.0, 110.0, 105.0]))

        result = agent.compute("AAPL", 3)

        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["source"], "test")
        self.assertEqual(result["n_days"], 3)
        self.assertEqual(result["last_price"], 105.0)
        self.assertAlmostEqual(result["total_return"], 0.05)
        for key in (
            "annualized_return",
            "annualized_volatility",
            "sharpe",
            "max_drawdown",
            "returns",
        ):
            self.assertIn(key, result)
        self.assertEqual(len(result["returns"]), 2)

    def test_insufficient_price_data_returns_error(self):
        agent = MetricsAgent(price_agent=FakePriceAgent([100.0]))

        result = agent.compute("AAPL", 1)

        self.assertEqual(result, {"ticker": "AAPL", "error": "insufficient price data"})


if __name__ == "__main__":
    unittest.main()
