"""Agent, tool and fan-out analytics from persisted execution observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from ..models import AnalyticsDataset, distribution


def agent_tool_latency_summary(dataset: AnalyticsDataset) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
    for observation in dataset.execution_observations:
        groups[(observation.agent, observation.tool)].append(observation)

    rows: list[dict[str, Any]] = []
    for (agent, tool), observations in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        latencies = [float(item.wall_time_ms or 0.0) for item in observations]
        cpu_ms = [float(item.cpu_time_ms or item.wall_time_ms or 0.0) for item in observations]
        rows.append(
            {
                "run_id": dataset.run.run_id,
                "agent": agent,
                "tool": tool,
                "calls": len(observations),
                "failures": sum(1 for item in observations if item.status != "success"),
                "wall_time_ms": sum(latencies),
                "cpu_time_ms": sum(cpu_ms),
                "latency_distribution_ms": distribution(latencies),
            }
        )
    return rows


def fanout_work_summary(dataset: AnalyticsDataset) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for observation in dataset.execution_observations:
        if observation.stage:
            groups[
                (observation.run_id, observation.request_id, observation.stage)
            ].append(observation)

    rows: list[dict[str, Any]] = []
    for (run_id, request_id, stage), observations in sorted(groups.items()):
        starts = [_parse(item.started_at) for item in observations]
        finishes = [_parse(item.finished_at) for item in observations]
        cumulative = sum(float(item.wall_time_ms or 0.0) for item in observations)
        critical = (max(finishes) - min(starts)).total_seconds() * 1000.0
        rows.append(
            {
                "run_id": run_id,
                "request_id": request_id,
                "stage": stage,
                "observation_count": len(observations),
                "cumulative_wall_time_ms": cumulative,
                "critical_path_wall_time_ms": critical,
            }
        )
    return rows


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
