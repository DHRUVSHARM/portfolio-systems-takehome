"""Configuration for the public Gateway service."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class GatewayConfig:
    portfolio_base_url: str = "http://portfolio-api:8000"
    max_in_flight: int = 64
    queue_capacity: int = 256
    queue_timeout_seconds: float = 5.0
    downstream_timeout_seconds: float = 60.0
    rate_limit_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.portfolio_base_url, str) or not self.portfolio_base_url:
            raise ValueError("portfolio_base_url must be a non-empty string")
        _validate_positive_int("max_in_flight", self.max_in_flight)
        if not isinstance(self.queue_capacity, int) or self.queue_capacity < 0:
            raise ValueError("queue_capacity must be a non-negative integer")
        _validate_positive_float("queue_timeout_seconds", self.queue_timeout_seconds)
        _validate_positive_float(
            "downstream_timeout_seconds", self.downstream_timeout_seconds
        )

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
            portfolio_base_url=os.getenv(
                "GATEWAY_PORTFOLIO_BASE_URL", "http://portfolio-api:8000"
            ),
            max_in_flight=_env_int("GATEWAY_MAX_IN_FLIGHT", default=64),
            queue_capacity=_env_int("GATEWAY_QUEUE_CAPACITY", default=256),
            queue_timeout_seconds=_env_float(
                "GATEWAY_QUEUE_TIMEOUT_SECONDS", default=5.0
            ),
            downstream_timeout_seconds=_env_float(
                "GATEWAY_DOWNSTREAM_TIMEOUT_SECONDS", default=60.0
            ),
            rate_limit_enabled=_env_bool("GATEWAY_RATE_LIMIT_ENABLED", default=False),
        )


def _validate_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validate_positive_float(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be positive")


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


def _env_int(name: str, *, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, *, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
