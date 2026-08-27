"""Versioned metric registry and calculator contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any

from .models import AnalyticsDataset, CostProfile, DerivedMetric


class MetricCalculator(Protocol):
    name: str
    version: str

    def calculate(
        self, dataset: AnalyticsDataset, context: "MetricContext"
    ) -> list[DerivedMetric]:
        ...


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    version: str
    description: str
    formula: str
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class MetricContext:
    cost_profile: CostProfile | None = None
    calculated_at: str | None = None
    percentiles: tuple[float, ...] = (0.50, 0.75, 0.90, 0.95, 0.99)


def default_metric_registry() -> tuple[MetricDefinition, ...]:
    return (
        MetricDefinition(
            name="total_infrastructure_cost_usd",
            version="1.0",
            description="Measured run duration in hours multiplied by machine hourly USD.",
            formula="duration_hours * cost_profile.machine_hourly_usd",
            inputs=("experiment_runs.started_at", "experiment_runs.finished_at", "cost_profiles"),
        ),
        MetricDefinition(
            name="cost_per_query_distribution_usd",
            version="1.0",
            description="Distribution over all attempted benchmark request cost allocations.",
            formula="percentiles(request_cost_attributions.total_cost_usd)",
            inputs=("requests", "request_cost_attributions"),
        ),
        MetricDefinition(
            name="agent_cost_percentage",
            version="1.0",
            description="Agent cost share inside the selected reporting boundary.",
            formula="agent_attributed_cost / total_run_cost * 100",
            inputs=("execution_observations", "inference_observations", "cost_profile"),
        ),
        MetricDefinition(
            name="agent_tool_latency",
            version="1.0",
            description="Calls, failures and latency percentiles grouped by agent/tool.",
            formula="group_by(agent, tool).percentiles(wall_time_ms)",
            inputs=("execution_observations",),
        ),
        MetricDefinition(
            name="fanout_cumulative_vs_critical_path_ms",
            version="1.0",
            description="Cumulative concurrent work compared with elapsed stage wall time.",
            formula="sum(child.wall_time_ms) vs max(child.finished_at)-min(child.started_at)",
            inputs=("execution_observations",),
        ),
        MetricDefinition(
            name="throughput_qps",
            version="1.0",
            description="Attempted benchmark requests divided by measured run duration.",
            formula="count(requests) / run.duration_seconds",
            inputs=("experiment_runs", "requests"),
        ),
        MetricDefinition(
            name="token_throughput",
            version="1.0",
            description="Prompt, completion and total token rates over measured run duration.",
            formula="sum(tokens) / run.duration_seconds",
            inputs=("experiment_runs", "inference_observations"),
        ),
        MetricDefinition(
            name="queries_per_dollar",
            version="1.0",
            description="Attempted benchmark requests divided by total run cost.",
            formula="count(requests) / total_infrastructure_cost_usd",
            inputs=("requests", "cost_profile"),
        ),
        MetricDefinition(
            name="tokens_per_dollar",
            version="1.0",
            description="Inference total tokens divided by total run cost.",
            formula="sum(total_tokens) / total_infrastructure_cost_usd",
            inputs=("inference_observations", "cost_profile"),
        ),
    )


def registry_as_rows() -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "version": item.version,
            "description": item.description,
            "formula": item.formula,
            "inputs": list(item.inputs),
        }
        for item in default_metric_registry()
    ]
