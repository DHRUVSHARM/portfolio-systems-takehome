"""Async closed-loop Gateway benchmark runner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any
from uuid import uuid4

import httpx

from .artifacts import write_run_artifacts
from .config import BenchmarkConfig
from .datasets import (
    load_query_records,
    normalized_payload,
    query_strata_summary,
    select_query_records,
)


@dataclass(frozen=True)
class RawRequestObservation:
    run_id: str
    request_id: str
    query_id: str
    n_holdings: int
    phrasing: str
    lookback_days: int
    start_timestamp: str
    finish_timestamp: str
    client_latency_ms: float
    http_status: int | None
    success: bool
    error_type: str | None
    response_body: Any = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "query_id": self.query_id,
            "n_holdings": self.n_holdings,
            "phrasing": self.phrasing,
            "lookback_days": self.lookback_days,
            "start_timestamp": self.start_timestamp,
            "finish_timestamp": self.finish_timestamp,
            "client_latency_ms": self.client_latency_ms,
            "http_status": self.http_status,
            "success": self.success,
            "error_type": self.error_type,
            "response_body": self.response_body,
        }


@dataclass(frozen=True)
class BenchmarkRunResult:
    run_id: str
    run_metadata: dict[str, Any]
    resolved_config: dict[str, Any]
    observations: list[RawRequestObservation]
    output_dir: str | None


async def run_benchmark(
    config: BenchmarkConfig,
    *,
    records: list[dict[str, Any]] | None = None,
    http_client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BenchmarkRunResult:
    """Run one closed-loop fixed-concurrency benchmark through Gateway."""

    run_id = config.resolved_run_id()
    corpus = load_query_records() if records is None else records
    selected, selection_metadata = select_query_records(corpus, config)
    resolved_config = config.as_resolved_dict(run_id=run_id)

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        base_url=config.gateway_base_url.rstrip("/"),
        timeout=config.request_timeout_seconds,
        transport=transport,
    )

    started_at = _utc_now()
    try:
        observations = await _run_closed_loop(
            client=client,
            run_id=run_id,
            records=selected,
            concurrency=config.concurrency,
        )
    finally:
        if owns_client:
            await client.aclose()
    finished_at = _utc_now()

    success_count = sum(1 for observation in observations if observation.success)
    failure_count = len(observations) - success_count
    run_metadata = {
        "run_id": run_id,
        "run_name": config.run_name,
        "dataset_mode": config.dataset_mode,
        "selected_query_count": len(selected),
        "selected_query_ids": selection_metadata["selected_query_ids"],
        "selection": selection_metadata,
        "strata": query_strata_summary(selected),
        "concurrency": config.concurrency,
        "started_at": started_at,
        "finished_at": finished_at,
        "request_count": len(observations),
        "success_count": success_count,
        "failure_count": failure_count,
        "artifacts": {
            "run": "run.json",
            "config": "resolved_benchmark_config.yaml",
            "requests": "requests.jsonl",
        },
    }

    output_dir = None
    if config.write_artifacts:
        output_path = write_run_artifacts(
            output_root=config.output_root_path(),
            run_id=run_id,
            run_metadata=run_metadata,
            resolved_config=resolved_config,
            observations=observations,
        )
        output_dir = str(output_path)

    return BenchmarkRunResult(
        run_id=run_id,
        run_metadata=run_metadata,
        resolved_config=resolved_config,
        observations=observations,
        output_dir=output_dir,
    )


async def _run_closed_loop(
    *,
    client: httpx.AsyncClient,
    run_id: str,
    records: list[dict[str, Any]],
    concurrency: int,
) -> list[RawRequestObservation]:
    queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
    for index, record in enumerate(records):
        queue.put_nowait((index, record))

    observations: list[RawRequestObservation | None] = [None] * len(records)

    async def worker() -> None:
        while True:
            try:
                index, record = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                observations[index] = await _issue_request(
                    client=client,
                    run_id=run_id,
                    sequence=index,
                    record=record,
                )
            finally:
                queue.task_done()

    worker_count = min(concurrency, len(records))
    if worker_count == 0:
        return []
    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return [observation for observation in observations if observation is not None]


async def _issue_request(
    *,
    client: httpx.AsyncClient,
    run_id: str,
    sequence: int,
    record: dict[str, Any],
) -> RawRequestObservation:
    payload, lookback_days = normalized_payload(record)
    query_id = str(record["id"])
    request_id = f"{run_id}-{sequence + 1}-{uuid4().hex}"
    headers = {
        "X-Run-ID": run_id,
        "X-Request-ID": request_id,
        "X-Query-ID": query_id,
    }
    start_timestamp = _utc_now()
    started = time.perf_counter()
    status: int | None = None
    success = False
    error_type: str | None = None
    response_body: Any = None

    try:
        response = await client.post("/v1/analyze", json=payload, headers=headers)
        status = response.status_code
        if 200 <= response.status_code < 300:
            try:
                response_body = response.json()
            except (ValueError, json.JSONDecodeError):
                error_type = "malformed_response"
            else:
                success = True
        else:
            error_type = _classify_status(response.status_code)
            try:
                response_body = response.json()
            except (ValueError, json.JSONDecodeError):
                response_body = response.text
    except httpx.TimeoutException:
        error_type = "timeout"
    except httpx.RequestError:
        error_type = "connection_failure"
    except asyncio.CancelledError:
        error_type = "cancelled"
        raise
    finally:
        finished = time.perf_counter()
        finish_timestamp = _utc_now()

    return RawRequestObservation(
        run_id=run_id,
        request_id=request_id,
        query_id=query_id,
        n_holdings=int(record["n_holdings"]),
        phrasing=str(record["phrasing"]),
        lookback_days=lookback_days,
        start_timestamp=start_timestamp,
        finish_timestamp=finish_timestamp,
        client_latency_ms=(finished - started) * 1000.0,
        http_status=status,
        success=success,
        error_type=error_type,
        response_body=response_body,
    )


def _classify_status(status_code: int) -> str:
    if status_code == 503:
        return "saturation"
    if status_code == 429:
        return "rate_limit"
    if 400 <= status_code < 500:
        return "other_4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return "unexpected_status"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
