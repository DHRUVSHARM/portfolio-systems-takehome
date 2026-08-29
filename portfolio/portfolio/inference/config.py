"""Configuration for OpenAI-compatible Advisor inference."""

from dataclasses import dataclass


CANONICAL_ADVISOR_MODEL = "Qwen/Qwen3-4B-Instruct-2507"


@dataclass(frozen=True)
class InferenceClientConfig:
    base_url: str = "http://vllm:8000/v1"
    model: str = CANONICAL_ADVISOR_MODEL
    timeout_seconds: float = 30.0
    max_tokens: int = 256
    temperature: float = 0.0
    api_key: str | None = None
    retry_count: int = 0
    enable_thinking: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if not isinstance(self.temperature, (int, float)) or self.temperature < 0:
            raise ValueError("temperature must be a non-negative number")
        if not isinstance(self.retry_count, int) or self.retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer")
        if self.enable_thinking is not None and not isinstance(self.enable_thinking, bool):
            raise ValueError("enable_thinking must be a boolean or None")

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"
