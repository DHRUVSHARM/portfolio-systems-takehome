# Portfolio analysis workflow deployed as a REST API endpoint.
#
# Fan-out / aggregate pipeline:
#   1. MetricsAgent  - per-ticker return/vol/Sharpe/drawdown (FAN-OUT, 1 per holding)
#      (MetricsAgent internally calls PriceAgent to fetch price history)
#   2. RiskAgent     - roll the per-ticker metrics into portfolio-level risk
#   3. AdvisorAgent  - LLM briefing (Bedrock) grounded in the computed figures
#
# The holdings map (ticker -> weight) and lookback window come in on the request
# body; the whole JSON body is splatted into main() as kwargs by deploy().
#
# Start agents first:  python -m ventis.controller.global_controller
# Test:
#   curl -X POST http://localhost:8080/main \
#        -H 'Content-Type: application/json' \
#        -d '{"holdings": {"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25}, "lookback_days": 180}'
#   curl http://localhost:8080/status/<request_id>

"""Local clean-version workflow entry point.

Ventis previously supplied remote Futures and serialized result transport.
This local version keeps the same fan-out/barrier ordering with standard
library futures and native Python dicts.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from ..agents.advisor_agent import AdvisorAgent
from ..agents.metrics_agent import MetricsAgent
from ..agents.risk_agent import RiskAgent


def main(
    holdings: dict = {"AAPL": 0.4, "MSFT": 0.35, "NVDA": 0.25},
    lookback_days: int = 365,
    metrics_agent: MetricsAgent | None = None,
    risk_agent: RiskAgent | None = None,
    advisor: AdvisorAgent | None = None,
    max_workers: int | None = None,
):
    metrics_agent = metrics_agent or MetricsAgent()
    risk_agent = risk_agent or RiskAgent()
    advisor = advisor or AdvisorAgent()

    tickers = list(holdings.keys())

    worker_count = max_workers or max(1, len(tickers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        metric_futures = {
            t: executor.submit(
                metrics_agent.compute, ticker=t, lookback_days=lookback_days
            )
            for t in tickers
        }
        per_ticker = {t: metric_futures[t].result() for t in tickers}

    # Stage 2: aggregate. RiskAgent needs every ticker's metrics (incl. the raw
    # return series) to build the covariance — this is the barrier.
    risk = risk_agent.assess(holdings=holdings, metrics=per_ticker)

    # Stage 3: LLM briefing grounded in the computed numbers.
    summary = advisor.summarize(holdings=holdings, metrics=per_ticker, risk=risk)

    # Drop the bulky raw return series from the API response.
    metrics_view = {
        t: {k: v for k, v in m.items() if k != "returns"} for t, m in per_ticker.items()
    }

    return {
        "holdings": holdings,
        "lookback_days": lookback_days,
        "metrics": metrics_view,
        "risk": risk,
        "summary": summary,
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
