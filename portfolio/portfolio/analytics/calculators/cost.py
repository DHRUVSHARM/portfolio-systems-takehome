"""Versioned infrastructure cost and attribution calculators."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from ..models import (
    AgentCost,
    AnalyticsDataset,
    CostAnalysis,
    CostProfile,
    DerivedMetric,
    RequestCost,
    distribution,
    now_utc,
    percentile,
)

REQUIRED_AGENTS = ("PriceAgent", "MetricsAgent", "RiskAgent", "AdvisorAgent")
ACCOUNTING_TOLERANCE = 1e-9


def total_run_cost_usd(dataset: AnalyticsDataset, profile: CostProfile) -> float:
    return (dataset.run.duration_seconds / 3600.0) * profile.machine_hourly_usd


def calculate_costs(dataset: AnalyticsDataset, profile: CostProfile) -> CostAnalysis:
    total = total_run_cost_usd(dataset, profile)
    request_costs = _allocate_request_costs(dataset, profile, total)
    agent_costs = _allocate_agent_costs(dataset, total)
    metrics = _assignment_metrics(dataset, profile, total, request_costs, agent_costs)
    return CostAnalysis(
        run_id=dataset.run.run_id,
        profile=profile,
        total_run_cost_usd=total,
        request_costs=tuple(request_costs),
        agent_costs=tuple(agent_costs),
        metrics=tuple(metrics),
    )


def recalculate_costs(dataset: AnalyticsDataset, profile: CostProfile) -> CostAnalysis:
    return calculate_costs(dataset, profile)


def _allocate_request_costs(
    dataset: AnalyticsDataset, profile: CostProfile, total: float
) -> list[RequestCost]:
    requests = list(dataset.requests)
    if not requests:
        return []

    cpu_pool = total * profile.cpu_pool_fraction
    gpu_pool = total * profile.gpu_pool_fraction
    overhead_pool = total * profile.overhead_pool_fraction

    cpu_seconds_by_request = _cpu_seconds_by_request(dataset)
    token_work_by_request = _token_work_by_request(dataset, profile)

    cpu_weights = {
        request.request_id: cpu_seconds_by_request.get(request.request_id, 0.0)
        for request in requests
    }
    token_weights = {
        request.request_id: token_work_by_request.get(request.request_id, 0.0)
        for request in requests
    }
    overhead_weights = {
        request.request_id: max(request.wall_seconds, 0.0)
        for request in requests
    }

    cpu_allocations = _allocate_pool(cpu_pool, requests, cpu_weights)
    gpu_allocations = _allocate_pool(gpu_pool, requests, token_weights)
    overhead_allocations = _allocate_pool(overhead_pool, requests, overhead_weights)

    rows: list[RequestCost] = []
    for index, request in enumerate(requests):
        cpu_cost = cpu_allocations[index]
        gpu_cost = gpu_allocations[index]
        overhead_cost = overhead_allocations[index]
        rows.append(
            RequestCost(
                run_id=request.run_id,
                request_id=request.request_id,
                query_id=request.query_id,
                success=request.success,
                total_cost_usd=cpu_cost + gpu_cost + overhead_cost,
                cpu_cost_usd=cpu_cost,
                gpu_cost_usd=gpu_cost,
                overhead_cost_usd=overhead_cost,
                cpu_seconds=cpu_weights[request.request_id],
                token_work=token_weights[request.request_id],
                wall_seconds=request.wall_seconds,
                raw={
                    "policy": "all attempted requests retain allocated infrastructure cost",
                    "profile": profile.profile_id,
                },
            )
        )

    residual = total - sum(row.total_cost_usd for row in rows)
    if rows and abs(residual) > ACCOUNTING_TOLERANCE:
        last = rows[-1]
        rows[-1] = RequestCost(
            **{
                **asdict(last),
                "total_cost_usd": last.total_cost_usd + residual,
                "overhead_cost_usd": last.overhead_cost_usd + residual,
            }
        )
    return rows


def _allocate_pool(
    pool: float, requests: list[Any], weights: dict[str, float]
) -> list[float]:
    if pool == 0.0:
        return [0.0 for _ in requests]
    total_weight = sum(max(weight, 0.0) for weight in weights.values())
    if total_weight <= 0.0:
        equal = pool / len(requests)
        allocations = [equal for _ in requests]
    else:
        allocations = [
            pool * max(weights[request.request_id], 0.0) / total_weight
            for request in requests
        ]
    residual = pool - sum(allocations)
    if allocations:
        allocations[-1] += residual
    return allocations


def _cpu_seconds_by_request(dataset: AnalyticsDataset) -> dict[str, float]:
    by_request: dict[str, float] = defaultdict(float)
    for observation in dataset.execution_observations:
        by_request[observation.request_id] += observation.cpu_seconds
    return dict(by_request)


def _token_work_by_request(
    dataset: AnalyticsDataset, profile: CostProfile
) -> dict[str, float]:
    by_request: dict[str, float] = defaultdict(float)
    for observation in dataset.inference_observations:
        prompt = observation.prompt_tokens or 0
        completion = observation.completion_tokens or 0
        by_request[observation.request_id] += (
            profile.prefill_token_weight * prompt
            + profile.decode_token_weight * completion
        )
    return dict(by_request)


def _allocate_agent_costs(dataset: AnalyticsDataset, total: float) -> list[AgentCost]:
    wall_by_agent: dict[str, float] = defaultdict(float)
    cpu_by_agent: dict[str, float] = defaultdict(float)
    latencies_by_agent: dict[str, list[float]] = defaultdict(list)
    failures_by_agent: dict[str, int] = defaultdict(int)
    calls_by_agent: dict[str, int] = defaultdict(int)

    for observation in dataset.execution_observations:
        wall = max(observation.wall_time_ms or 0.0, 0.0)
        cpu = max(observation.cpu_time_ms or wall, 0.0)
        wall_by_agent[observation.agent] += wall
        cpu_by_agent[observation.agent] += cpu
        latencies_by_agent[observation.agent].append(wall)
        calls_by_agent[observation.agent] += 1
        if observation.status != "success":
            failures_by_agent[observation.agent] += 1

    for observation in dataset.inference_observations:
        wall = max(observation.elapsed_ms, 0.0)
        wall_by_agent[observation.agent] += wall
        latencies_by_agent[observation.agent].append(wall)
        calls_by_agent[observation.agent] += 1
        if observation.status < 200 or observation.status >= 300:
            failures_by_agent[observation.agent] += 1

    for agent in REQUIRED_AGENTS:
        wall_by_agent.setdefault(agent, 0.0)
        cpu_by_agent.setdefault(agent, 0.0)
        latencies_by_agent.setdefault(agent, [])
        failures_by_agent.setdefault(agent, 0)
        calls_by_agent.setdefault(agent, 0)

    total_weight = sum(wall_by_agent.values())
    if total_weight <= 0.0:
        wall_by_agent["overhead_unallocated"] = 1.0
        latencies_by_agent["overhead_unallocated"] = []
        calls_by_agent["overhead_unallocated"] = 0
        failures_by_agent["overhead_unallocated"] = 0
        cpu_by_agent["overhead_unallocated"] = 0.0
        total_weight = 1.0

    rows: list[AgentCost] = []
    for agent in sorted(wall_by_agent):
        cost = total * wall_by_agent[agent] / total_weight
        percent = 0.0 if total == 0.0 else cost / total * 100.0
        latencies = latencies_by_agent[agent]
        rows.append(
            AgentCost(
                run_id=dataset.run.run_id,
                agent=agent,
                calls=calls_by_agent[agent],
                wall_time_ms=wall_by_agent[agent],
                cpu_time_ms=cpu_by_agent[agent],
                p50_latency_ms=percentile(latencies, 0.50),
                p95_latency_ms=percentile(latencies, 0.95),
                p99_latency_ms=percentile(latencies, 0.99),
                failures=failures_by_agent[agent],
                attributed_cost_usd=cost,
                cost_percentage=percent,
                raw={"boundary": "execution and inference observations"},
            )
        )

    residual = total - sum(row.attributed_cost_usd for row in rows)
    if rows and abs(residual) > ACCOUNTING_TOLERANCE:
        last = rows[-1]
        new_cost = last.attributed_cost_usd + residual
        rows[-1] = AgentCost(
            **{
                **asdict(last),
                "attributed_cost_usd": new_cost,
                "cost_percentage": 0.0 if total == 0.0 else new_cost / total * 100.0,
            }
        )
    return rows


def _assignment_metrics(
    dataset: AnalyticsDataset,
    profile: CostProfile,
    total: float,
    request_costs: list[RequestCost],
    agent_costs: list[AgentCost],
) -> list[DerivedMetric]:
    calculated_at = now_utc()
    cost_values = [row.total_cost_usd for row in request_costs]
    cost_dist = distribution(cost_values)
    success_cost_values = [
        row.total_cost_usd for row in request_costs if row.success
    ]
    agent_percentages = {
        row.agent: row.cost_percentage for row in agent_costs
    }
    return [
        _metric(dataset, profile, "total_infrastructure_cost_usd", total, calculated_at),
        _metric(
            dataset,
            profile,
            "cost_per_query_attempt_usd",
            {
                row.query_id: row.total_cost_usd
                for row in request_costs
            },
            calculated_at,
            raw={"failed_requests": "included"},
        ),
        _metric(
            dataset,
            profile,
            "cost_per_successful_query_usd",
            distribution(success_cost_values),
            calculated_at,
            raw={"failed_requests": "excluded from successful-only statistic"},
        ),
        _metric(
            dataset,
            profile,
            "cost_per_query_distribution_usd",
            cost_dist,
            calculated_at,
            raw={"failed_requests": "included"},
        ),
        _metric(
            dataset,
            profile,
            "agent_cost_percentage",
            agent_percentages,
            calculated_at,
        ),
        _metric(
            dataset,
            profile,
            "queries_per_dollar",
            0.0 if total == 0.0 else len(dataset.requests) / total,
            calculated_at,
        ),
        _metric(
            dataset,
            profile,
            "tokens_per_dollar",
            _tokens_per_dollar(dataset, total),
            calculated_at,
        ),
    ]


def _metric(
    dataset: AnalyticsDataset,
    profile: CostProfile,
    name: str,
    value: Any,
    calculated_at: str,
    raw: dict[str, Any] | None = None,
) -> DerivedMetric:
    return DerivedMetric(
        run_id=dataset.run.run_id,
        name=name,
        version="1.0",
        value=value,
        calculated_at=calculated_at,
        cost_profile_name=profile.name,
        cost_profile_version=profile.version,
        raw=raw or {},
    )


def _tokens_per_dollar(dataset: AnalyticsDataset, total: float) -> float:
    if total == 0.0:
        return 0.0
    tokens = sum(item.total_tokens or 0 for item in dataset.inference_observations)
    return tokens / total
