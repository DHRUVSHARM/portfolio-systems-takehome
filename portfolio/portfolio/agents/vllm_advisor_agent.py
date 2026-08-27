"""Async vLLM/OpenAI-compatible Advisor implementation."""

from __future__ import annotations

from collections.abc import Callable
import inspect

from .advisor_prompt import build_advisor_prompt
from ..inference import (
    InferenceClientConfig,
    InferenceObservation,
    OpenAICompatibleInferenceClient,
)
from ..observability import log_event, portfolio_metrics, start_as_current_span
from ..service.context import RequestContext


ObservationSink = Callable[[InferenceObservation], object]


class VLLMAdvisorAgent:
    def __init__(
        self,
        *,
        client: OpenAICompatibleInferenceClient | None = None,
        config: InferenceClientConfig | None = None,
        observation_sink: ObservationSink | None = None,
    ) -> None:
        if client is None:
            client = OpenAICompatibleInferenceClient(config or InferenceClientConfig())
        self.client = client
        self.observation_sink = observation_sink or _noop_observation_sink

    async def summarize_async(
        self,
        holdings: dict,
        metrics: dict,
        risk: dict,
        context: RequestContext | None = None,
    ) -> str:
        context = context or RequestContext()
        with start_as_current_span(
            "AdvisorAgent.summarize",
            {
                "agent": "AdvisorAgent",
                "stage": "advisor",
                "n_holdings": len(holdings),
                "run_id": context.run_id,
                "request_id": context.request_id,
                "query_id": context.query_id,
            },
        ), portfolio_metrics.agent_timer(agent="AdvisorAgent"), portfolio_metrics.tool_timer(
            agent="AdvisorAgent", tool="summarize"
        ):
            prompt = build_advisor_prompt(holdings=holdings, metrics=metrics, risk=risk)
            result = await self.client.chat_completion(prompt)
            observation = InferenceObservation(
                run_id=context.run_id,
                request_id=context.request_id,
                query_id=context.query_id,
                result=result,
            )
            maybe_awaitable = self.observation_sink(observation)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
            log_event(
                logger_name="portfolio.advisor",
                event="advisor_inference_complete",
                context=context,
                stage="advisor",
                agent="AdvisorAgent",
                model=result.model,
                status=result.status,
                duration=result.elapsed_ms / 1000.0,
            )
            return result.text

    async def aclose(self) -> None:
        await self.client.aclose()


def _noop_observation_sink(observation: InferenceObservation) -> None:
    del observation
