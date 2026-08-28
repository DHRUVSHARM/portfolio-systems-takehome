"""Request correlation context shared across async and worker-thread spans."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_request_context: ContextVar[Any | None] = ContextVar(
    "portfolio_request_context",
    default=None,
)


def set_request_context(context: Any | None) -> Token:
    return _request_context.set(context)


def reset_request_context(token: Token) -> None:
    _request_context.reset(token)


def correlation_attributes() -> dict[str, str]:
    context = _request_context.get()
    if context is None:
        return {}
    return {
        key: value
        for key, value in {
            "run_id": getattr(context, "run_id", None),
            "request_id": getattr(context, "request_id", None),
            "query_id": getattr(context, "query_id", None),
        }.items()
        if value is not None
    }
