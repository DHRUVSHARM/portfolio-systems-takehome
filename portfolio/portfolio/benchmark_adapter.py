"""Deterministic adapter from supplied query records to workflow input.

This module is benchmark plumbing, not a workflow agent. It uses the structured
metadata in queries.json plus a fixed ticker/name map to produce the portfolio
workflow's native input contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


SUPPORTED_PHRASINGS = {"percent", "equal", "unweighted"}
WEIGHT_TOLERANCE = 1e-6

COMPANY_TO_TICKER = {
    "adobe": "ADBE",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "apple": "AAPL",
    "bank of america": "BAC",
    "berkshire hathaway": "BRK.B",
    "chevron": "CVX",
    "cisco": "CSCO",
    "coca-cola": "KO",
    "costco": "COST",
    "disney": "DIS",
    "exxonmobil": "XOM",
    "intel": "INTC",
    "johnson & johnson": "JNJ",
    "jpmorgan": "JPM",
    "mastercard": "MA",
    "meta": "META",
    "microsoft": "MSFT",
    "netflix": "NFLX",
    "nvidia": "NVDA",
    "oracle": "ORCL",
    "pepsico": "PEP",
    "pfizer": "PFE",
    "qualcomm": "QCOM",
    "salesforce": "CRM",
    "tesla": "TSLA",
    "unitedhealth": "UNH",
    "visa": "V",
    "walmart": "WMT",
}

TICKERS = {
    "AAPL",
    "ADBE",
    "AMD",
    "AMZN",
    "BAC",
    "BRK.B",
    "COST",
    "CRM",
    "CSCO",
    "CVX",
    "DIS",
    "GOOGL",
    "INTC",
    "JNJ",
    "JPM",
    "KO",
    "MA",
    "META",
    "MSFT",
    "NFLX",
    "NVDA",
    "ORCL",
    "PEP",
    "PFE",
    "QCOM",
    "TSLA",
    "UNH",
    "V",
    "WMT",
    "XOM",
}


class QueryAdapterError(ValueError):
    """Raised when a benchmark query record cannot be safely normalized."""


@dataclass(frozen=True)
class NormalizedPortfolio:
    record_id: int
    holdings: dict[str, float]
    lookback_days: int | None

    def to_workflow_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"holdings": self.holdings}
        if self.lookback_days is not None:
            kwargs["lookback_days"] = self.lookback_days
        return kwargs


@dataclass(frozen=True)
class _Mention:
    ticker: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Percent:
    value: float
    start: int
    end: int


def normalize_query_record(record: dict[str, Any]) -> NormalizedPortfolio:
    """Convert one supplied queries.json record to portfolio workflow input."""
    _validate_record_shape(record)

    record_id = int(record["id"])
    query = record["query"]
    phrasing = record["phrasing"]
    expected_count = int(record["n_holdings"])
    lookback_days = _normalize_lookback(record["expected_lookback_days"])

    mentions = _find_asset_mentions(query)
    if len(mentions) != expected_count:
        raise QueryAdapterError(
            f"record {record_id}: parsed {len(mentions)} holdings, "
            f"expected {expected_count}"
        )

    if phrasing == "percent":
        holdings = _parse_percent_holdings(record_id, query, mentions)
    else:
        weight = 1.0 / expected_count
        holdings = {mention.ticker: weight for mention in mentions}

    _validate_holdings(record_id, holdings, expected_count)
    return NormalizedPortfolio(
        record_id=record_id,
        holdings=holdings,
        lookback_days=lookback_days,
    )


def normalize_query_records(records: list[dict[str, Any]]) -> list[NormalizedPortfolio]:
    return [normalize_query_record(record) for record in records]


def _validate_record_shape(record: dict[str, Any]) -> None:
    required = {"id", "query", "n_holdings", "phrasing", "expected_lookback_days"}
    missing = required.difference(record)
    if missing:
        raise QueryAdapterError(f"query record missing required fields: {sorted(missing)}")
    if record["phrasing"] not in SUPPORTED_PHRASINGS:
        raise QueryAdapterError(f"unsupported phrasing: {record['phrasing']!r}")
    if not isinstance(record["query"], str) or not record["query"].strip():
        raise QueryAdapterError("query must be a non-empty string")
    if not isinstance(record["n_holdings"], int) or record["n_holdings"] <= 0:
        raise QueryAdapterError("n_holdings must be a positive integer")


def _normalize_lookback(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise QueryAdapterError("expected_lookback_days must be a positive integer or null")
    return value


def _find_asset_mentions(query: str) -> list[_Mention]:
    aliases: dict[str, str] = {}
    aliases.update(COMPANY_TO_TICKER)
    aliases.update({ticker.lower(): ticker for ticker in TICKERS})

    choices = sorted(aliases, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![A-Za-z0-9])("
        + "|".join(re.escape(choice) for choice in choices)
        + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )

    mentions: list[_Mention] = []
    seen: set[str] = set()
    for match in pattern.finditer(query):
        alias = match.group(1).lower()
        ticker = aliases[alias]
        if ticker in seen:
            raise QueryAdapterError(f"duplicate ticker mention: {ticker}")
        seen.add(ticker)
        mentions.append(_Mention(ticker, match.group(1), match.start(), match.end()))
    return mentions


def _parse_percent_holdings(
    record_id: int, query: str, mentions: list[_Mention]
) -> dict[str, float]:
    percentages = [
        _Percent(float(match.group(1)), match.start(), match.end())
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%", query)
    ]
    if len(percentages) != len(mentions):
        raise QueryAdapterError(
            f"record {record_id}: parsed {len(percentages)} percentages "
            f"for {len(mentions)} holdings"
        )

    unassigned = list(mentions)
    raw_weights: dict[str, float] = {}
    for percent in percentages:
        mention = min(unassigned, key=lambda item: _span_gap(percent, item))
        unassigned.remove(mention)
        if percent.value <= 0:
            raise QueryAdapterError(f"record {record_id}: percentage must be positive")
        raw_weights[mention.ticker] = percent.value / 100.0

    total = sum(raw_weights.values())
    if total <= 0:
        raise QueryAdapterError(f"record {record_id}: percentages sum to zero")
    return {ticker: weight / total for ticker, weight in raw_weights.items()}


def _span_gap(percent: _Percent, mention: _Mention) -> int:
    if percent.end <= mention.start:
        return mention.start - percent.end
    if mention.end <= percent.start:
        return percent.start - mention.end
    return 0


def _validate_holdings(
    record_id: int, holdings: dict[str, float], expected_count: int
) -> None:
    if len(holdings) != expected_count:
        raise QueryAdapterError(
            f"record {record_id}: normalized {len(holdings)} holdings, "
            f"expected {expected_count}"
        )
    unknown = sorted(set(holdings).difference(TICKERS))
    if unknown:
        raise QueryAdapterError(f"record {record_id}: unknown tickers {unknown}")
    if any(weight <= 0 for weight in holdings.values()):
        raise QueryAdapterError(f"record {record_id}: all weights must be positive")
    total = sum(holdings.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise QueryAdapterError(
            f"record {record_id}: normalized weights sum to {total}, expected 1.0"
        )
