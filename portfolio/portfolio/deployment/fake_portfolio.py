"""Cheap fake Portfolio API for Gateway-only stress tests."""

from __future__ import annotations

import asyncio
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..observability import ObservabilityConfig, configure_tracing, inject_trace_context
from ..observability import configure_json_logging, log_event, start_as_current_span


telemetry = ObservabilityConfig.from_env(service_name="fake-portfolio")
configure_json_logging(enabled=telemetry.json_logging)
configure_tracing(telemetry)

app = FastAPI(title="Fake Portfolio API", version="0.1.0")
_started = time.time()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "fake-portfolio"}


@app.get("/ready")
async def ready() -> dict:
    return {"ready": True, "uptime_seconds": time.time() - _started}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(b"", media_type="text/plain; version=0.0.4")


@app.post("/internal/analyze")
async def analyze(request: Request) -> JSONResponse:
    payload = await request.json()
    delay = float(os.getenv("FAKE_PORTFOLIO_DELAY_SECONDS", "0.05"))
    status_code = int(os.getenv("FAKE_PORTFOLIO_STATUS_CODE", "200"))
    headers = {
        "X-Run-ID": request.headers.get("X-Run-ID", ""),
        "X-Request-ID": request.headers.get("X-Request-ID", ""),
        "X-Query-ID": request.headers.get("X-Query-ID", ""),
    }
    with start_as_current_span(
        "fake_portfolio.analyze",
        {
            "run_id": headers["X-Run-ID"],
            "request_id": headers["X-Request-ID"],
            "query_id": headers["X-Query-ID"],
            "stage": "fake_portfolio",
        },
    ):
        await asyncio.sleep(max(0.0, delay))
        log_event(
            "fake_portfolio_response",
            request_id=headers["X-Request-ID"],
            query_id=headers["X-Query-ID"],
            status_code=status_code,
        )
        response_headers = inject_trace_context(headers)
        if status_code >= 400:
            return JSONResponse(
                {"error": "fake_portfolio_error"},
                status_code=status_code,
                headers=response_headers,
            )
        holdings = payload.get("holdings", {})
        return JSONResponse(
            {
                "metrics": {},
                "risk": {"n_holdings": len(holdings)},
                "advice": "Fake Portfolio response for Gateway-only stress mode.",
            },
            headers=response_headers,
        )
