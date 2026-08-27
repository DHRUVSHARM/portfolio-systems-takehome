"""FastAPI public Gateway for portfolio analysis."""

from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from opentelemetry import context as otel_context

from portfolio.portfolio.observability import (
    ObservabilityConfig,
    configure_json_logging,
    configure_tracing,
    extract_trace_context,
    gateway_metrics,
    inject_trace_context,
    log_event,
    render_gateway_metrics,
    start_as_current_span,
)
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
    observability_config: ObservabilityConfig | None = None,
) -> FastAPI:
    gateway_config = config or GatewayConfig.from_env()
    telemetry_config = observability_config or ObservabilityConfig.from_env(
        service_name="gateway"
    )
    factory = client_factory or (lambda cfg: PortfolioApiClient(cfg))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        configure_json_logging(
            service=telemetry_config.service_name,
            enabled=telemetry_config.json_logging,
        )
        configure_tracing(telemetry_config)
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

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=render_gateway_metrics(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/analyze")
    async def analyze(
        payload: AnalyzeRequest,
        request: Request,
        response: Response,
        x_run_id: str | None = Header(default=None, alias="X-Run-ID"),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
        x_query_id: str | None = Header(default=None, alias="X-Query-ID"),
    ):
        request_id = x_request_id or str(uuid4())
        correlation_headers = {"X-Request-ID": request_id}
        if x_run_id is not None:
            correlation_headers["X-Run-ID"] = x_run_id
        if x_query_id is not None:
            correlation_headers["X-Query-ID"] = x_query_id
        started = time.perf_counter()
        status_code = 500
        token = otel_context.attach(extract_trace_context(request.headers))

        try:
            with start_as_current_span(
                "POST /v1/analyze",
                {
                    "http.route": "/v1/analyze",
                    "run_id": x_run_id,
                    "request_id": request_id,
                    "query_id": x_query_id,
                },
            ) as span:
                try:
                    with start_as_current_span(
                    "gateway.validation",
                    {"stage": "validation", "request_id": request_id},
                    ):
                        try:
                            holdings, lookback_days = validate_analyze_request(payload)
                        except ValueError as exc:
                            status_code = 422
                            raise HTTPException(status_code=422, detail=str(exc)) from exc

                    span.set_attribute("n_holdings", len(holdings))
                    span.set_attribute("lookback_days", lookback_days)

                    if not getattr(request.app.state, "ready", False):
                        status_code = 503
                        raise HTTPException(status_code=503, detail="service is not ready")

                    admission: AdmissionController = request.app.state.admission
                    wait_started = time.perf_counter()
                    try:
                        with start_as_current_span(
                            "gateway.admission_wait",
                            {"stage": "admission", "request_id": request_id},
                        ):
                            lease = await admission.acquire(
                                queue_timeout_seconds=gateway_config.queue_timeout_seconds
                            )
                    except AdmissionRejected as exc:
                        status_code = 503
                        gateway_metrics.admission_rejections_total.labels("capacity").inc()
                        gateway_metrics.queue_wait_duration_seconds.labels("rejected").observe(
                            time.perf_counter() - wait_started
                        )
                        raise HTTPException(
                            status_code=503,
                            detail="gateway admission capacity exhausted",
                            headers=correlation_headers,
                        ) from exc
                    except AdmissionQueueTimeout as exc:
                        status_code = 503
                        gateway_metrics.queue_timeouts_total.inc()
                        gateway_metrics.queue_wait_duration_seconds.labels("timeout").observe(
                            time.perf_counter() - wait_started
                        )
                        raise HTTPException(
                            status_code=503,
                            detail="gateway admission queue timed out",
                            headers=correlation_headers,
                        ) from exc

                    async with lease:
                        gateway_metrics.queue_wait_duration_seconds.labels("admitted").observe(
                            time.perf_counter() - wait_started
                        )
                        snapshot = await admission.snapshot()
                        gateway_metrics.set_admission_snapshot(
                            active=snapshot.active, waiting=snapshot.waiting
                        )

                        response.headers["X-Request-ID"] = request_id
                        if x_run_id is not None:
                            response.headers["X-Run-ID"] = x_run_id
                        if x_query_id is not None:
                            response.headers["X-Query-ID"] = x_query_id

                        downstream_started = time.perf_counter()
                        downstream_status_class = "unknown"
                        try:
                            downstream = await request.app.state.portfolio_client.analyze(
                                payload={
                                    "holdings": holdings,
                                    "lookback_days": lookback_days,
                                },
                                headers=inject_trace_context(correlation_headers),
                            )
                            downstream_status_class = f"{downstream.status_code // 100}xx"
                        except DownstreamTimeoutError as exc:
                            status_code = 504
                            gateway_metrics.downstream_failures_total.labels("timeout").inc()
                            raise HTTPException(
                                status_code=504,
                                detail="portfolio request timed out",
                                headers=correlation_headers,
                            ) from exc
                        except DownstreamConnectionError as exc:
                            status_code = 502
                            gateway_metrics.downstream_failures_total.labels(
                                "connection_failure"
                            ).inc()
                            raise HTTPException(
                                status_code=502,
                                detail="portfolio request failed",
                                headers=correlation_headers,
                            ) from exc
                        finally:
                            downstream_elapsed = time.perf_counter() - downstream_started
                            gateway_metrics.downstream_duration_seconds.labels(
                                downstream_status_class
                            ).observe(downstream_elapsed)

                        for header_name, header_value in downstream.headers.items():
                            response.headers[header_name] = header_value

                        if downstream.status_code < 200 or downstream.status_code >= 300:
                            status_code = 502
                            gateway_metrics.downstream_failures_total.labels(
                                f"{downstream.status_code // 100}xx"
                            ).inc()
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

                        status_code = 200
                        log_event(
                            logger_name="gateway",
                            event="gateway_request_completed",
                            stage="gateway",
                            status="success",
                            run_id=x_run_id,
                            request_id=request_id,
                            query_id=x_query_id,
                        )
                        return downstream.body
                finally:
                    span.set_attribute("http.status_code", status_code)
                    gateway_metrics.record_request(
                        endpoint="/v1/analyze",
                        method="POST",
                        status_code=status_code,
                        duration_seconds=time.perf_counter() - started,
                    )
                    admission = getattr(request.app.state, "admission", None)
                    if admission is not None:
                        snapshot = await admission.snapshot()
                        gateway_metrics.set_admission_snapshot(
                            active=snapshot.active, waiting=snapshot.waiting
                        )
        finally:
            otel_context.detach(token)

    return app


app = create_app()
