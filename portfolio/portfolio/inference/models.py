"""Typed inference result and observation records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InferenceResult:
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    elapsed_ms: float
    status: int
    attempt_count: int
    retry_count: int


@dataclass(frozen=True)
class InferenceObservation:
    run_id: str | None
    request_id: str | None
    query_id: str | None
    result: InferenceResult
