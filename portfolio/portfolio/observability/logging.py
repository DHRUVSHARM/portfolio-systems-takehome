"""Structured JSON logging with trace and request correlation fields."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "event": record.getMessage(),
        }
        data.update(_trace_fields())
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            data.update({key: value for key, value in extra.items() if value is not None})
        if record.exc_info:
            data["error_type"] = record.exc_info[0].__name__
        return json.dumps(data, sort_keys=True)


def configure_json_logging(*, service: str, enabled: bool = True) -> None:
    if not enabled:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def log_event(
    *,
    logger_name: str,
    event: str,
    context: Any | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    structured = dict(fields)
    if context is not None:
        structured.update(
            {
                "run_id": context.run_id,
                "request_id": context.request_id,
                "query_id": context.query_id,
            }
        )
    logging.getLogger(logger_name).log(level, event, extra={"structured": structured})


def _trace_fields() -> dict[str, str]:
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }
