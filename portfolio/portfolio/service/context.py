"""Request correlation context for future service layers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    run_id: str | None = None
    request_id: str | None = None
    query_id: str | None = None
