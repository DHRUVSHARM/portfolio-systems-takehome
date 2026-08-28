"""Async OpenAI-compatible chat completion client."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import InferenceClientConfig
from .models import InferenceResult
from ..observability import start_as_current_span


class InferenceError(RuntimeError):
    """Base class for inference failures."""


class InferenceConnectionError(InferenceError):
    """Raised when the inference endpoint cannot be reached or times out."""


class InferenceHTTPStatusError(InferenceError):
    """Raised when the inference endpoint returns a non-2xx response."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class InferenceResponseError(InferenceError):
    """Raised when the inference response shape is malformed."""


class OpenAICompatibleInferenceClient:
    def __init__(
        self,
        config: InferenceClientConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            transport=transport,
        )
        self._closed = False

    async def chat_completion(
        self,
        prompt: str,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        query_id: str | None = None,
    ) -> InferenceResult:
        if self._closed:
            raise RuntimeError("inference client is closed")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be a non-empty string")

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if self.config.enable_thinking is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.config.enable_thinking,
            }

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        max_attempts = self.config.retry_count + 1
        last_error: InferenceError | None = None
        started = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            span_attributes = {
                "model": self.config.model,
                "inference.retry_count": self.config.retry_count,
                "inference.attempt": attempt,
            }
            if run_id:
                span_attributes["run_id"] = run_id
            if request_id:
                span_attributes["request_id"] = request_id
            if query_id:
                span_attributes["query_id"] = query_id

            with start_as_current_span(
                "inference.request",
                span_attributes,
            ) as span:
                try:
                    response = await self._client.post(
                        self.config.chat_completions_url,
                        json=payload,
                        headers=headers,
                    )
                except httpx.TimeoutException as exc:
                    last_error = InferenceConnectionError(
                        f"inference request timed out: {exc}"
                    )
                except httpx.RequestError as exc:
                    last_error = InferenceConnectionError(
                        f"inference request failed: {exc}"
                    )
                else:
                    span.set_attribute("http.status_code", response.status_code)
                    if response.status_code < 200 or response.status_code >= 300:
                        raise InferenceHTTPStatusError(
                            response.status_code,
                            f"inference endpoint returned HTTP {response.status_code}",
                        )
                    finished = time.perf_counter()
                    result = self._parse_response(
                        response=response,
                        elapsed_ms=(finished - started) * 1000.0,
                        attempt_count=attempt,
                    )
                    span.set_attribute("llm.prompt_tokens", result.prompt_tokens or 0)
                    span.set_attribute(
                        "llm.completion_tokens", result.completion_tokens or 0
                    )
                    span.set_attribute("llm.total_tokens", result.total_tokens or 0)
                    return result

            if attempt == max_attempts and last_error is not None:
                raise last_error

        raise InferenceConnectionError("inference request failed")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def _parse_response(
        self,
        *,
        response: httpx.Response,
        elapsed_ms: float,
        attempt_count: int,
    ) -> InferenceResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise InferenceResponseError("inference response was not valid JSON") from exc

        try:
            choices = data["choices"]
            text = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceResponseError(
                "inference response did not include a completion message"
            ) from exc
        if not isinstance(text, str) or not text:
            raise InferenceResponseError("inference completion text was empty")

        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise InferenceResponseError("inference usage field was malformed")

        return InferenceResult(
            text=text,
            model=str(data.get("model") or self.config.model),
            prompt_tokens=_nullable_int(usage.get("prompt_tokens")),
            completion_tokens=_nullable_int(usage.get("completion_tokens")),
            total_tokens=_nullable_int(usage.get("total_tokens")),
            elapsed_ms=elapsed_ms,
            status=response.status_code,
            attempt_count=attempt_count,
            retry_count=attempt_count - 1,
        )


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise InferenceResponseError("inference usage token count was malformed")
    return value
