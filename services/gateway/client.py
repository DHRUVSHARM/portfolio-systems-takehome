"""Process-lifetime downstream client for the internal Portfolio API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from portfolio.portfolio.observability import start_as_current_span

from .config import GatewayConfig


class DownstreamError(RuntimeError):
    """Base class for downstream Portfolio API failures."""


class DownstreamConnectionError(DownstreamError):
    """Raised when Gateway cannot connect to Portfolio API."""


class DownstreamTimeoutError(DownstreamError):
    """Raised when Portfolio API exceeds the downstream timeout."""


@dataclass(frozen=True)
class DownstreamResult:
    status_code: int
    body: Any
    headers: dict[str, str]


class PortfolioApiClient:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=config.portfolio_base_url.rstrip("/"),
            timeout=config.downstream_timeout_seconds,
            transport=transport,
        )
        self._closed = False

    async def analyze(
        self,
        *,
        payload: dict,
        headers: dict[str, str],
    ) -> DownstreamResult:
        if self._closed:
            raise RuntimeError("portfolio client is closed")

        try:
            with start_as_current_span(
                "portfolio.request",
                {
                    "stage": "portfolio",
                    "n_holdings": len(payload.get("holdings", {})),
                    "lookback_days": payload.get("lookback_days"),
                },
            ):
                response = await self._client.post(
                    "/internal/analyze",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise DownstreamTimeoutError("portfolio request timed out") from exc
        except httpx.RequestError as exc:
            raise DownstreamConnectionError("portfolio request failed") from exc

        try:
            body = response.json()
        except ValueError:
            body = {"detail": "portfolio response was not valid JSON"}

        return DownstreamResult(
            status_code=response.status_code,
            body=body,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"x-run-id", "x-request-id", "x-query-id"}
            },
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()
