"""FastAPI application for the internal Portfolio API."""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response

from portfolio.portfolio.service import PortfolioRuntime, RequestContext

from .config import PortfolioApiConfig, build_portfolio_runtime
from .models import AnalyzeRequest, validate_analyze_request


RuntimeFactory = Callable[[PortfolioApiConfig], PortfolioRuntime]


def create_app(
    *,
    config: PortfolioApiConfig | None = None,
    runtime_factory: RuntimeFactory = build_portfolio_runtime,
) -> FastAPI:
    """Create an internal API app with process-lifetime workflow resources."""

    service_config = config or PortfolioApiConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = False
        runtime = runtime_factory(service_config)
        app.state.runtime = runtime
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            await _close_runtime(runtime)

    app = FastAPI(title="Internal Portfolio API", lifespan=lifespan)
    app.state.ready = False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        if not getattr(app.state, "ready", False):
            raise HTTPException(status_code=503, detail="service is not ready")
        return {"status": "ready"}

    @app.post("/internal/analyze")
    async def analyze(
        payload: AnalyzeRequest,
        request: Request,
        response: Response,
        x_run_id: str | None = Header(default=None, alias="X-Run-ID"),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
        x_query_id: str | None = Header(default=None, alias="X-Query-ID"),
    ) -> dict:
        try:
            holdings, lookback_days = validate_analyze_request(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        request_id = x_request_id or str(uuid4())
        context = RequestContext(
            run_id=x_run_id,
            request_id=request_id,
            query_id=x_query_id,
        )
        response.headers["X-Request-ID"] = request_id
        if x_run_id is not None:
            response.headers["X-Run-ID"] = x_run_id
        if x_query_id is not None:
            response.headers["X-Query-ID"] = x_query_id

        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None or not getattr(request.app.state, "ready", False):
            raise HTTPException(status_code=503, detail="service is not ready")

        try:
            return await runtime.analyze(
                holdings=holdings,
                lookback_days=lookback_days,
                context=context,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="portfolio workflow failed"
            ) from exc

    return app


async def _close_runtime(runtime: PortfolioRuntime) -> None:
    close = getattr(runtime, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
        return

    shutdown = getattr(runtime, "shutdown", None)
    if shutdown is not None:
        shutdown()


app = create_app()
