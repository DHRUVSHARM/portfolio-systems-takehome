"""Derived report helpers built only from persisted-style datasets."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from ..calculators.agents import agent_tool_latency_summary, fanout_work_summary
from ..models import AnalyticsDataset, CostAnalysis


def build_run_report(dataset: AnalyticsDataset, analysis: CostAnalysis) -> dict[str, Any]:
    metrics = {item.name: item.value for item in analysis.metrics}
    return {
        "run_id": dataset.run.run_id,
        "dataset_mode": dataset.run.dataset_mode,
        "request_count": len(dataset.requests),
        "success_count": sum(1 for item in dataset.requests if item.success),
        "failure_count": sum(1 for item in dataset.requests if not item.success),
        "cost_profile": analysis.profile.profile_id,
        "total_run_cost_usd": analysis.total_run_cost_usd,
        "request_cost_sum_usd": analysis.request_cost_sum_usd,
        "assignment_metrics": metrics,
        "agent_costs": [asdict(item) for item in analysis.agent_costs],
        "agent_tool_latency": agent_tool_latency_summary(dataset),
        "fanout_work": fanout_work_summary(dataset),
    }


def build_report_from_artifacts(output_dir: Path | str) -> dict[str, Any]:
    from .parquet import read_parquet_rows

    run_dir = Path(output_dir)
    run = json.loads((run_dir / "run.json").read_text())
    requests = read_parquet_rows(run_dir / "requests.parquet")
    request_costs = read_parquet_rows(run_dir / "request_cost_attributions.parquet")
    agent_costs = read_parquet_rows(run_dir / "agent_cost_attributions.parquet")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    return {
        "run_id": run["run_id"],
        "request_count": len(requests),
        "success_count": sum(1 for row in requests if row["success"]),
        "failure_count": sum(1 for row in requests if not row["success"]),
        "total_run_cost_usd": sum(row["total_cost_usd"] for row in request_costs),
        "assignment_metrics": metrics["metrics"],
        "agent_costs": agent_costs,
    }
