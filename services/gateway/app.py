"""FastAPI public Gateway for portfolio analysis."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .admission import AdmissionController, AdmissionQueueTimeout, AdmissionRejected
from .client import (
    DownstreamConnectionError,
    DownstreamTimeoutError,
    PortfolioApiClient,
)
from .config import GatewayConfig
from .models import AnalyzeRequest, validate_analyze_request


ClientFactory = Callable[[GatewayConfig], PortfolioApiClient]


def create_app(
    *,
    config: GatewayConfig | None = None,
    client_factory: ClientFactory | None = None,
) -> FastAPI:
    gateway_config = config or GatewayConfig.from_env()
    factory = client_factory or (lambda cfg: PortfolioApiClient(cfg))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        app.state.admission = AdmissionController(
            max_in_flight=gateway_config.max_in_flight,
            queue_capacity=gateway_config.queue_capacity,
        )
        app.state.portfolio_client = factory(gateway_config)
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            await app.state.portfolio_client.aclose()

    app = FastAPI(title="Portfolio Gateway", lifespan=lifespan)
    app.state.ready = False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not getattr(app.state, "ready", False):
            raise HTTPException(status_code=503, detail="service is not ready")
        return {"status": "ready"}

    @app.post("/v1/analyze")
    async def analyze(
        payload: AnalyzeRequest,
        request: Request,
        response: Response,
        x_run_id: str | None = Header(default=None, alias="X-Run-ID"),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
        x_query_id: str | None = Header(default=None, alias="X-Query-ID"),
    ):
        try:
            holdings, lookback_days = validate_analyze_request(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not getattr(request.app.state, "ready", False):
            raise HTTPException(status_code=503, detail="service is not ready")

        request_id = x_request_id or str(uuid4())
        correlation_headers = {"X-Request-ID": request_id}
        if x_run_id is not None:
            correlation_headers["X-Run-ID"] = x_run_id
        if x_query_id is not None:
            correlation_headers["X-Query-ID"] = x_query_id

        admission: AdmissionController = request.app.state.admission
        try:
            lease = await admission.acquire(
                queue_timeout_seconds=gateway_config.queue_timeout_seconds
            )
        except AdmissionRejected as exc:
            raise HTTPException(
                status_code=503,
                detail="gateway admission capacity exhausted",
                headers=correlation_headers,
            ) from exc
        except AdmissionQueueTimeout as exc:
            raise HTTPException(
                status_code=503,
                detail="gateway admission queue timed out",
                headers=correlation_headers,
            ) from exc

        async with lease:
            response.headers["X-Request-ID"] = request_id
            if x_run_id is not None:
                response.headers["X-Run-ID"] = x_run_id
            if x_query_id is not None:
                response.headers["X-Query-ID"] = x_query_id

            try:
                downstream = await request.app.state.portfolio_client.analyze(
                    payload={
                        "holdings": holdings,
                        "lookback_days": lookback_days,
                    },
                    headers=correlation_headers,
                )
            except DownstreamTimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail="portfolio request timed out",
                    headers=correlation_headers,
                ) from exc
            except DownstreamConnectionError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="portfolio request failed",
                    headers=correlation_headers,
                ) from exc

            for header_name, header_value in downstream.headers.items():
                response.headers[header_name] = header_value

            if downstream.status_code < 200 or downstream.status_code >= 300:
                return JSONResponse(
                    status_code=502,
                    content={
                        "detail": "portfolio API returned non-success status",
                        "downstream_status_code": downstream.status_code,
                    },
                    headers={
                        name: value
                        for name, value in response.headers.items()
                        if name.lower()
                        in {"x-run-id", "x-request-id", "x-query-id"}
                    },
                )

            return downstream.body

    return app


app = create_app()
