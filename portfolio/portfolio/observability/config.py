"""Configuration for application observability."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ObservabilityConfig:
    service_name: str
    environment: str = "local"
    tracing_enabled: bool = True
    tracing_sample_ratio: float = 1.0
    otlp_endpoint: str | None = None
    json_logging: bool = True

    @classmethod
    def from_env(cls, *, service_name: str) -> "ObservabilityConfig":
        return cls(
            service_name=service_name,
            environment=os.getenv("OBSERVABILITY_ENVIRONMENT", "local"),
            tracing_enabled=_env_bool("OTEL_TRACES_ENABLED", default=True),
            tracing_sample_ratio=_env_float("OTEL_TRACES_SAMPLER_ARG", default=1.0),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            json_logging=_env_bool("JSON_LOGGING_ENABLED", default=True),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.service_name, str) or not self.service_name:
            raise ValueError("service_name must be a non-empty string")
        if self.tracing_sample_ratio < 0.0 or self.tracing_sample_ratio > 1.0:
            raise ValueError("tracing_sample_ratio must be between 0 and 1")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_float(name: str, *, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
