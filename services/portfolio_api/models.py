"""Request models and validation for the internal Portfolio API."""

from __future__ import annotations

from typing import Any
import math

from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    holdings: dict[str, Any]
    lookback_days: Any = 365


def validate_analyze_request(payload: AnalyzeRequest) -> tuple[dict[str, float], int]:
    holdings = _validate_holdings(payload.holdings)
    lookback_days = _validate_lookback_days(payload.lookback_days)
    return holdings, lookback_days


def _validate_holdings(raw: dict[str, Any]) -> dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("holdings must be a non-empty object")

    holdings: dict[str, float] = {}
    for ticker, weight in raw.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("holding tickers must be non-empty strings")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError("holding weights must be numeric")

        numeric_weight = float(weight)
        if not math.isfinite(numeric_weight) or numeric_weight <= 0:
            raise ValueError("holding weights must be positive finite numbers")
        holdings[ticker] = numeric_weight

    return holdings


def _validate_lookback_days(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("lookback_days must be a positive integer")
    if raw <= 0:
        raise ValueError("lookback_days must be a positive integer")
    return raw
