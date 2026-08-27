"""Low-cardinality Prometheus application metrics."""

from __future__ import annotations

from contextlib import contextmanager
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class GatewayMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests_total = Counter(
            "gateway_requests_total",
            "Gateway HTTP requests.",
            ("endpoint", "method", "status_class", "status_code"),
            registry=self.registry,
        )
        self.request_duration_seconds = Histogram(
            "gateway_request_duration_seconds",
            "Gateway request duration.",
            ("endpoint", "method", "status_class"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.inflight = Gauge(
            "gateway_inflight_requests",
            "Gateway active downstream requests.",
            registry=self.registry,
        )
        self.queued = Gauge(
            "gateway_queued_requests",
            "Gateway queued requests.",
            registry=self.registry,
        )
        self.queue_wait_duration_seconds = Histogram(
            "gateway_queue_wait_duration_seconds",
            "Gateway admission queue wait duration.",
            ("outcome",),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.admission_rejections_total = Counter(
            "gateway_admission_rejections_total",
            "Gateway admission rejections.",
            ("reason",),
            registry=self.registry,
        )
        self.queue_timeouts_total = Counter(
            "gateway_queue_timeouts_total",
            "Gateway admission queue timeouts.",
            registry=self.registry,
        )
        self.downstream_duration_seconds = Histogram(
            "gateway_downstream_duration_seconds",
            "Gateway downstream Portfolio API duration.",
            ("status_class",),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.downstream_failures_total = Counter(
            "gateway_downstream_failures_total",
            "Gateway downstream failures.",
            ("error_type",),
            registry=self.registry,
        )

    def record_request(
        self, *, endpoint: str, method: str, status_code: int, duration_seconds: float
    ) -> None:
        status_class = _status_class(status_code)
        self.requests_total.labels(endpoint, method, status_class, str(status_code)).inc()
        self.request_duration_seconds.labels(endpoint, method, status_class).observe(
            duration_seconds
        )

    def set_admission_snapshot(self, *, active: int, waiting: int) -> None:
        self.inflight.set(active)
        self.queued.set(waiting)


class PortfolioMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.workflows_active = Gauge(
            "portfolio_workflows_active",
            "Portfolio workflows currently active.",
            registry=self.registry,
        )
        self.workflows_total = Counter(
            "portfolio_workflows_total",
            "Portfolio workflows completed.",
            ("status",),
            registry=self.registry,
        )
        self.workflow_duration_seconds = Histogram(
            "portfolio_workflow_duration_seconds",
            "Portfolio workflow duration.",
            ("status",),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.agent_calls_total = Counter(
            "portfolio_agent_calls_total",
            "Portfolio agent calls.",
            ("agent", "status"),
            registry=self.registry,
        )
        self.agent_call_duration_seconds = Histogram(
            "portfolio_agent_call_duration_seconds",
            "Portfolio agent call duration.",
            ("agent", "status"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.agent_errors_total = Counter(
            "portfolio_agent_errors_total",
            "Portfolio agent errors.",
            ("agent", "error_type"),
            registry=self.registry,
        )
        self.tool_calls_total = Counter(
            "portfolio_tool_calls_total",
            "Portfolio tool calls.",
            ("agent", "tool", "status"),
            registry=self.registry,
        )
        self.tool_call_duration_seconds = Histogram(
            "portfolio_tool_call_duration_seconds",
            "Portfolio tool call duration.",
            ("agent", "tool", "status"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.tool_errors_total = Counter(
            "portfolio_tool_errors_total",
            "Portfolio tool errors.",
            ("agent", "tool", "error_type"),
            registry=self.registry,
        )
        self.metric_tasks_running = Gauge(
            "portfolio_metric_tasks_running",
            "MetricsAgent tasks running.",
            registry=self.registry,
        )
        self.metric_tasks_waiting = Gauge(
            "portfolio_metric_tasks_waiting",
            "MetricsAgent tasks waiting for admission.",
            registry=self.registry,
        )
        self.cpu_slots_used = Gauge(
            "portfolio_workflow_cpu_slots_used",
            "Workflow CPU executor slots in use.",
            registry=self.registry,
        )
        self.cpu_slots_waiting = Gauge(
            "portfolio_workflow_cpu_slots_waiting",
            "Workflow CPU submissions waiting for admission.",
            registry=self.registry,
        )

    @contextmanager
    def workflow_timer(self):
        self.workflows_active.inc()
        started = time.perf_counter()
        status = "success"
        try:
            yield
        except BaseException:
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - started
            self.workflows_active.dec()
            self.workflows_total.labels(status).inc()
            self.workflow_duration_seconds.labels(status).observe(duration)

    @contextmanager
    def agent_timer(self, *, agent: str):
        started = time.perf_counter()
        status = "success"
        try:
            yield
        except BaseException as exc:
            status = "error"
            self.agent_errors_total.labels(agent, type(exc).__name__).inc()
            raise
        finally:
            duration = time.perf_counter() - started
            self.agent_calls_total.labels(agent, status).inc()
            self.agent_call_duration_seconds.labels(agent, status).observe(duration)

    @contextmanager
    def tool_timer(self, *, agent: str, tool: str):
        started = time.perf_counter()
        status = "success"
        try:
            yield
        except BaseException as exc:
            status = "error"
            self.tool_errors_total.labels(agent, tool, type(exc).__name__).inc()
            raise
        finally:
            duration = time.perf_counter() - started
            self.tool_calls_total.labels(agent, tool, status).inc()
            self.tool_call_duration_seconds.labels(agent, tool, status).observe(duration)


gateway_metrics = GatewayMetrics()
portfolio_metrics = PortfolioMetrics()


def render_gateway_metrics() -> bytes:
    return generate_latest(gateway_metrics.registry)


def render_portfolio_metrics() -> bytes:
    return generate_latest(portfolio_metrics.registry)


def _status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"
