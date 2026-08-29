"""OpenTelemetry tracing setup and safe span helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from opentelemetry import context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased, TraceIdRatioBased

from .config import ObservabilityConfig


_memory_exporter: InMemorySpanExporter | None = None
_configured = False
_tracing_enabled = True
_sample_ratio = 1.0


def configure_tracing(
    config: ObservabilityConfig,
    *,
    in_memory: bool = False,
    console_fallback: bool = False,
) -> None:
    """Configure tracing once; exporter failures must not affect serving."""

    global _configured, _memory_exporter, _tracing_enabled, _sample_ratio
    _tracing_enabled = config.tracing_enabled
    _sample_ratio = config.tracing_sample_ratio
    if _configured:
        return

    sampler = ALWAYS_OFF
    if config.tracing_enabled:
        sampler = ParentBased(TraceIdRatioBased(config.tracing_sample_ratio))

    provider = TracerProvider(
        sampler=sampler,
        resource=Resource.create(
            {
                "service.name": config.service_name,
                "deployment.environment": config.environment,
            }
        ),
    )
    if in_memory:
        _memory_exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))
    elif config.otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otlp_endpoint))
            )
        except Exception:
            if console_fallback:
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif console_fallback:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _configured = True


def reset_observability_for_tests() -> None:
    global _configured, _memory_exporter, _tracing_enabled, _sample_ratio
    _configured = False
    _memory_exporter = None
    _tracing_enabled = True
    _sample_ratio = 1.0
    try:
        trace._TRACER_PROVIDER = None
        trace._TRACER_PROVIDER_SET_ONCE._done = False
    except Exception:
        pass


def get_finished_spans():
    if _memory_exporter is None:
        return []
    return list(_memory_exporter.get_finished_spans())


def get_tracer(name: str = "portfolio-systems"):
    return trace.get_tracer(name)


@contextmanager
def start_as_current_span(name: str, attributes: dict[str, Any] | None = None):
    """Start a span while making telemetry failures non-fatal."""

    if not _tracing_enabled or _sample_ratio <= 0:
        yield trace.INVALID_SPAN
        return

    try:
        manager = get_tracer().start_as_current_span(name)
        span = manager.__enter__()
    except Exception:
        yield trace.INVALID_SPAN
        return

    try:
        try:
            if attributes:
                for key, value in attributes.items():
                    if value is not None:
                        span.set_attribute(key, value)
        except Exception:
            pass
        try:
            yield span
        except BaseException as exc:
            try:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass
    finally:
        pass


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    propagated = dict(headers)
    try:
        propagate.inject(propagated)
    except Exception:
        return headers
    return propagated


def extract_trace_context(headers) -> context.Context:
    try:
        return propagate.extract(headers)
    except Exception:
        return context.get_current()
