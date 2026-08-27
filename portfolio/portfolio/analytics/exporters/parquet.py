"""Portable Parquet and report artifacts for Phase 7 analytics."""

from __future__ import annotations

from dataclasses import asdict
import csv
import json
from pathlib import Path
from typing import Any

from ..calculators.agents import agent_tool_latency_summary, fanout_work_summary
from ..models import AnalyticsDataset, CostAnalysis, distribution
from ..registry import registry_as_rows


def write_parquet_run_artifacts(
    *,
    output_dir: Path | str,
    dataset: AnalyticsDataset,
    analysis: CostAnalysis | None = None,
) -> Path:
    """Write immutable raw observations and derived Phase 7 artifacts."""

    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    run_json = {
        **asdict(dataset.run),
        "artifacts": {
            "requests": "requests.parquet",
            "execution_observations": "execution_observations.parquet",
            "inference_observations": "inference_observations.parquet",
            "resource_samples": "resource_samples.parquet",
            "metrics": "metrics.json",
            "summary": "summary.csv",
            "charts": "charts/",
        },
    }
    _write_json(run_dir / "run.json", run_json)
    _write_rows(run_dir / "requests.parquet", [asdict(item) for item in dataset.requests])
    _write_rows(
        run_dir / "execution_observations.parquet",
        [asdict(item) for item in dataset.execution_observations],
    )
    _write_rows(
        run_dir / "inference_observations.parquet",
        [asdict(item) for item in dataset.inference_observations],
    )
    _write_rows(
        run_dir / "resource_samples.parquet",
        [asdict(item) for item in dataset.resource_samples],
    )
    _write_rows(run_dir / "metric_registry.parquet", registry_as_rows())

    if analysis is not None:
        _write_rows(
            run_dir / "request_cost_attributions.parquet",
            [asdict(item) for item in analysis.request_costs],
        )
        _write_rows(
            run_dir / "agent_cost_attributions.parquet",
            [asdict(item) for item in analysis.agent_costs],
        )
        _write_json(
            run_dir / "metrics.json",
            {
                "run_id": analysis.run_id,
                "cost_profile": analysis.profile.profile_id,
                "metrics": [asdict(item) for item in analysis.metrics],
            },
        )
        _write_summary_csv(run_dir / "summary.csv", dataset, analysis)
        _write_chart_data(run_dir / "charts", dataset, analysis)
    else:
        _write_json(run_dir / "metrics.json", {"run_id": dataset.run.run_id, "metrics": []})
        _write_summary_csv(run_dir / "summary.csv", dataset, None)
        (run_dir / "charts").mkdir(exist_ok=True)

    return run_dir


def read_parquet_rows(path: Path | str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    return table.to_pylist()


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    normalized = [_json_safe_row(row) for row in rows]
    if normalized:
        table = pa.Table.from_pylist(normalized)
    else:
        table = pa.table({})
    pq.write_table(table, path)


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    safe = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple)):
            safe[key] = json.dumps(value, sort_keys=True)
        else:
            safe[key] = value
    return safe


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")


def _write_summary_csv(
    path: Path, dataset: AnalyticsDataset, analysis: CostAnalysis | None
) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "run_id",
                "request_count",
                "success_count",
                "failure_count",
                "total_run_cost_usd",
                "request_cost_sum_usd",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": dataset.run.run_id,
                "request_count": len(dataset.requests),
                "success_count": sum(1 for item in dataset.requests if item.success),
                "failure_count": sum(1 for item in dataset.requests if not item.success),
                "total_run_cost_usd": analysis.total_run_cost_usd if analysis else "",
                "request_cost_sum_usd": analysis.request_cost_sum_usd if analysis else "",
            }
        )


def _write_chart_data(
    charts_dir: Path, dataset: AnalyticsDataset, analysis: CostAnalysis
) -> None:
    charts_dir.mkdir(exist_ok=True)
    costs = [row.total_cost_usd for row in analysis.request_costs]
    _write_json(
        charts_dir / "cost_query_histogram.json",
        {"run_id": analysis.run_id, "bins": _histogram(costs)},
    )
    _write_json(
        charts_dir / "cost_query_boxplot.json",
        {"run_id": analysis.run_id, "values": costs},
    )
    _write_json(
        charts_dir / "cost_query_cdf.json",
        {
            "run_id": analysis.run_id,
            "points": [
                {"cost_usd": value, "cdf": (index + 1) / len(costs)}
                for index, value in enumerate(sorted(costs))
            ]
            if costs
            else [],
        },
    )
    _write_json(
        charts_dir / "latency_distribution.json",
        {
            "run_id": dataset.run.run_id,
            "values_ms": [row.client_latency_ms for row in dataset.requests],
        },
    )
    _write_json(
        charts_dir / "cost_latency_by_holdings.json",
        {
            "run_id": analysis.run_id,
            "points": [
                {
                    "request_id": row.request_id,
                    "query_id": row.query_id,
                    "n_holdings": request.n_holdings,
                    "latency_ms": request.client_latency_ms,
                    "cost_usd": row.total_cost_usd,
                }
                for row, request in zip(analysis.request_costs, dataset.requests)
            ],
        },
    )
    _write_json(
        charts_dir / "query_type_breakdown.json",
        {
            "run_id": analysis.run_id,
            "groups": _query_breakdown(
                dataset=dataset,
                analysis=analysis,
                key_fn=lambda request: request.phrasing,
            ),
        },
    )
    _write_json(
        charts_dir / "holdings_count_breakdown.json",
        {
            "run_id": analysis.run_id,
            "groups": _query_breakdown(
                dataset=dataset,
                analysis=analysis,
                key_fn=lambda request: str(request.n_holdings),
            ),
        },
    )
    inference_by_request = {row.request_id: row for row in dataset.inference_observations}
    _write_json(
        charts_dir / "cost_vs_tokens.json",
        {
            "run_id": analysis.run_id,
            "points": [
                {
                    "request_id": row.request_id,
                    "query_id": row.query_id,
                    "prompt_tokens": getattr(inference_by_request.get(row.request_id), "prompt_tokens", None),
                    "completion_tokens": getattr(
                        inference_by_request.get(row.request_id), "completion_tokens", None
                    ),
                    "cost_usd": row.total_cost_usd,
                }
                for row in analysis.request_costs
            ],
        },
    )
    _write_json(
        charts_dir / "agent_cost_share.json",
        {
            "run_id": analysis.run_id,
            "agents": [
                {
                    "agent": row.agent,
                    "cost_usd": row.attributed_cost_usd,
                    "cost_percentage": row.cost_percentage,
                }
                for row in analysis.agent_costs
            ],
        },
    )
    _write_json(
        charts_dir / "agent_tool_latency.json",
        {"run_id": dataset.run.run_id, "groups": agent_tool_latency_summary(dataset)},
    )
    _write_json(
        charts_dir / "fanout_work.json",
        {"run_id": dataset.run.run_id, "groups": fanout_work_summary(dataset)},
    )


def _histogram(values: list[float], bins: int = 10) -> list[dict[str, float | int]]:
    if not values:
        return []
    lower = min(values)
    upper = max(values)
    if lower == upper:
        return [{"lower": lower, "upper": upper, "count": len(values)}]
    width = (upper - lower) / bins
    counts = [0 for _ in range(bins)]
    for value in values:
        index = min(int((value - lower) / width), bins - 1)
        counts[index] += 1
    return [
        {
            "lower": lower + index * width,
            "upper": lower + (index + 1) * width,
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _query_breakdown(dataset: AnalyticsDataset, analysis: CostAnalysis, key_fn):
    cost_by_request = {row.request_id: row for row in analysis.request_costs}
    inference_by_request = {row.request_id: row for row in dataset.inference_observations}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for request in dataset.requests:
        group = key_fn(request) or "unknown"
        cost = cost_by_request.get(request.request_id)
        inference = inference_by_request.get(request.request_id)
        grouped.setdefault(group, []).append(
            {
                "success": request.success,
                "latency_ms": request.client_latency_ms,
                "cost_usd": cost.total_cost_usd if cost else None,
                "prompt_tokens": inference.prompt_tokens if inference else None,
                "completion_tokens": inference.completion_tokens if inference else None,
            }
        )

    rows = []
    for group, items in sorted(grouped.items()):
        costs = [item["cost_usd"] for item in items if item["cost_usd"] is not None]
        latencies = [item["latency_ms"] for item in items]
        prompt_tokens = [
            item["prompt_tokens"] for item in items if item["prompt_tokens"] is not None
        ]
        completion_tokens = [
            item["completion_tokens"]
            for item in items
            if item["completion_tokens"] is not None
        ]
        cost_dist = distribution(costs)
        latency_dist = distribution(latencies)
        rows.append(
            {
                "group": group,
                "count": len(items),
                "success_rate": sum(1 for item in items if item["success"]) / len(items),
                "mean_cost_per_query_usd": cost_dist["mean"],
                "median_cost_per_query_usd": cost_dist["median"],
                "p95_cost_per_query_usd": cost_dist["p95"],
                "mean_latency_ms": latency_dist["mean"],
                "p95_latency_ms": latency_dist["p95"],
                "average_prompt_tokens": _average(prompt_tokens),
                "average_completion_tokens": _average(completion_tokens),
            }
        )
    return rows


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
