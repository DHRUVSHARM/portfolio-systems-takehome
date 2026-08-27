"""Configuration and runtime ownership for the internal Portfolio API."""

from __future__ import annotations

from dataclasses import dataclass
import os

from portfolio.portfolio.agents.metrics_agent import MetricsAgent
from portfolio.portfolio.agents.price_agent import PriceAgent
from portfolio.portfolio.agents.risk_agent import RiskAgent
from portfolio.portfolio.agents.vllm_advisor_agent import VLLMAdvisorAgent
from portfolio.portfolio.inference import (
    InferenceClientConfig,
    OpenAICompatibleInferenceClient,
)
from portfolio.portfolio.service import PortfolioRuntime, WorkflowRuntimeConfig


@dataclass(frozen=True)
class PortfolioApiConfig:
    """External settings needed to construct one process-owned runtime."""

    use_yfinance: bool = False
    cpu_workers: int = 8
    max_concurrent_metric_tasks: int = 16
    inference_base_url: str = "http://vllm:8000/v1"
    inference_model: str = "Qwen/Qwen3-4B-Instruct-2507"
    inference_timeout_seconds: float = 30.0
    inference_max_tokens: int = 256
    inference_temperature: float = 0.0
    inference_api_key: str | None = None
    inference_retry_count: int = 0

    @classmethod
    def from_env(cls) -> "PortfolioApiConfig":
        return cls(
            use_yfinance=_env_bool("PORTFOLIO_USE_YFINANCE", default=False),
            cpu_workers=_env_int("PORTFOLIO_CPU_WORKERS", default=8),
            max_concurrent_metric_tasks=_env_int(
                "PORTFOLIO_MAX_CONCURRENT_METRIC_TASKS", default=16
            ),
            inference_base_url=os.getenv(
                "PORTFOLIO_INFERENCE_BASE_URL", "http://vllm:8000/v1"
            ),
            inference_model=os.getenv(
                "PORTFOLIO_INFERENCE_MODEL", "Qwen/Qwen3-4B-Instruct-2507"
            ),
            inference_timeout_seconds=_env_float(
                "PORTFOLIO_INFERENCE_TIMEOUT_SECONDS", default=30.0
            ),
            inference_max_tokens=_env_int(
                "PORTFOLIO_INFERENCE_MAX_TOKENS", default=256
            ),
            inference_temperature=_env_float(
                "PORTFOLIO_INFERENCE_TEMPERATURE", default=0.0
            ),
            inference_api_key=os.getenv("PORTFOLIO_INFERENCE_API_KEY") or None,
            inference_retry_count=_env_int(
                "PORTFOLIO_INFERENCE_RETRY_COUNT", default=0
            ),
        )


def build_portfolio_runtime(config: PortfolioApiConfig) -> PortfolioRuntime:
    """Build the process-lifetime workflow objects owned by the service."""

    price_agent = PriceAgent(use_yfinance=config.use_yfinance)
    metrics_agent = MetricsAgent(price_agent=price_agent)
    risk_agent = RiskAgent()
    inference_config = InferenceClientConfig(
        base_url=config.inference_base_url,
        model=config.inference_model,
        timeout_seconds=config.inference_timeout_seconds,
        max_tokens=config.inference_max_tokens,
        temperature=config.inference_temperature,
        api_key=config.inference_api_key,
        retry_count=config.inference_retry_count,
    )
    inference_client = OpenAICompatibleInferenceClient(inference_config)
    advisor = VLLMAdvisorAgent(client=inference_client)

    return PortfolioRuntime(
        config=WorkflowRuntimeConfig(
            cpu_workers=config.cpu_workers,
            max_concurrent_metric_tasks=config.max_concurrent_metric_tasks,
        ),
        metrics_agent=metrics_agent,
        risk_agent=risk_agent,
        advisor=advisor,
    )


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
