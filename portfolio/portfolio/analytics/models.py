"""Raw Phase 7 observation and derived analytics records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import sqrt
from statistics import median
from typing import Any


RawDict = dict[str, Any]


@dataclass(frozen=True)
class ExperimentRun:
    run_id: str
    started_at: str
    finished_at: str
    status: str = "completed"
    invalid_reason: str | None = None
    run_name: str | None = None
    dataset_mode: str | None = None
    selected_query_count: int | None = None
    selection_manifest: RawDict | None = None
    sample_seed: int | None = None
    benchmark_concurrency: int | None = None
    gateway_max_in_flight: int | None = None
    gateway_queue_capacity: int | None = None
    workflow_cpu_workers: int | None = None
    max_concurrent_metric_tasks: int | None = None
    model: str | None = None
    model_revision: str | None = None
    vllm_version: str | None = None
    dtype: str | None = None
    max_model_len: int | None = None
    max_num_seqs: int | None = None
    max_num_batched_tokens: int | None = None
    gpu_memory_utilization: float | None = None
    prefix_caching_enabled: bool | None = None
    hardware_profile: RawDict | None = None
    git_commit: str | None = None
    config_hashes: RawDict | None = None
    cost_profile_name: str | None = None
    cost_profile_version: str | None = None
    raw: RawDict = field(default_factory=dict)

    @classmethod
    def from_benchmark_metadata(cls, metadata: RawDict) -> "ExperimentRun":
        return cls(
            run_id=str(metadata["run_id"]),
            started_at=str(metadata["started_at"]),
            finished_at=str(metadata["finished_at"]),
            status=str(metadata.get("status") or "completed"),
            invalid_reason=metadata.get("invalid_reason"),
            run_name=metadata.get("run_name"),
            dataset_mode=metadata.get("dataset_mode"),
            selected_query_count=_optional_int(metadata.get("selected_query_count")),
            selection_manifest=metadata.get("selection"),
            sample_seed=_optional_int(metadata.get("sample_seed")),
            benchmark_concurrency=_optional_int(metadata.get("concurrency")),
            gateway_max_in_flight=_optional_int(metadata.get("gateway_max_in_flight")),
            gateway_queue_capacity=_optional_int(metadata.get("gateway_queue_capacity")),
            workflow_cpu_workers=_optional_int(metadata.get("workflow_cpu_workers")),
            max_concurrent_metric_tasks=_optional_int(
                metadata.get("max_concurrent_metric_tasks")
            ),
            model=metadata.get("model"),
            model_revision=metadata.get("model_revision"),
            vllm_version=metadata.get("vllm_version"),
            dtype=metadata.get("dtype"),
            max_model_len=_optional_int(metadata.get("max_model_len")),
            max_num_seqs=_optional_int(metadata.get("max_num_seqs")),
            max_num_batched_tokens=_optional_int(metadata.get("max_num_batched_tokens")),
            gpu_memory_utilization=_optional_float(
                metadata.get("gpu_memory_utilization")
            ),
            prefix_caching_enabled=metadata.get("prefix_caching_enabled"),
            hardware_profile=metadata.get("hardware_profile"),
            git_commit=metadata.get("git_commit"),
            config_hashes=metadata.get("config_hashes"),
            cost_profile_name=metadata.get("cost_profile_name"),
            cost_profile_version=metadata.get("cost_profile_version"),
            raw=dict(metadata),
        )

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (_parse_ts(self.finished_at) - _parse_ts(self.started_at)).total_seconds())


@dataclass(frozen=True)
class RequestObservation:
    run_id: str
    request_id: str
    query_id: str
    n_holdings: int
    phrasing: str
    lookback_days: int
    start_timestamp: str
    finish_timestamp: str
    client_latency_ms: float
    http_status: int | None
    success: bool
    error_type: str | None = None
    gateway_latency_ms: float | None = None
    gateway_queue_wait_ms: float | None = None
    response_body: Any = None
    raw: RawDict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, observation: Any) -> "RequestObservation":
        row = (
            observation.to_json_dict()
            if hasattr(observation, "to_json_dict")
            else dict(observation)
        )
        return cls(
            run_id=str(row["run_id"]),
            request_id=str(row["request_id"]),
            query_id=str(row["query_id"]),
            n_holdings=int(row["n_holdings"]),
            phrasing=str(row.get("phrasing") or ""),
            lookback_days=int(row["lookback_days"]),
            start_timestamp=str(row["start_timestamp"]),
            finish_timestamp=str(row["finish_timestamp"]),
            client_latency_ms=float(row["client_latency_ms"]),
            http_status=_optional_int(row.get("http_status")),
            success=bool(row["success"]),
            error_type=row.get("error_type"),
            gateway_latency_ms=_optional_float(row.get("gateway_latency_ms")),
            gateway_queue_wait_ms=_optional_float(row.get("gateway_queue_wait_ms")),
            response_body=row.get("response_body"),
            raw=dict(row),
        )

    @property
    def wall_seconds(self) -> float:
        return self.client_latency_ms / 1000.0


@dataclass(frozen=True)
class ExecutionObservation:
    run_id: str
    request_id: str
    query_id: str
    stage: str
    agent: str
    started_at: str
    finished_at: str
    observation_id: str | None = None
    parent_observation_id: str | None = None
    tool: str | None = None
    ticker: str | None = None
    wall_time_ms: float | None = None
    cpu_time_ms: float | None = None
    status: str = "success"
    error_type: str | None = None
    raw: RawDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.wall_time_ms is None:
            object.__setattr__(self, "wall_time_ms", self.inferred_wall_time_ms)

    @property
    def inferred_wall_time_ms(self) -> float:
        return (
            _parse_ts(self.finished_at) - _parse_ts(self.started_at)
        ).total_seconds() * 1000.0

    @property
    def cpu_seconds(self) -> float:
        if self.cpu_time_ms is not None:
            return max(0.0, self.cpu_time_ms / 1000.0)
        return max(0.0, (self.wall_time_ms or 0.0) / 1000.0)


@dataclass(frozen=True)
class InferenceObservationRecord:
    run_id: str
    request_id: str
    query_id: str
    model: str
    elapsed_ms: float
    status: int
    agent: str = "AdvisorAgent"
    started_at: str | None = None
    finished_at: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    ttft_ms: float | None = None
    queue_ms: float | None = None
    prefill_ms: float | None = None
    decode_ms: float | None = None
    generation_ms: float | None = None
    mean_itl_ms: float | None = None
    tpot_ms: float | None = None
    tokens_per_second: float | None = None
    error_type: str | None = None
    attempt_count: int = 1
    retry_count: int = 0
    raw: RawDict = field(default_factory=dict)

    @classmethod
    def from_inference_observation(cls, observation: Any) -> "InferenceObservationRecord":
        result = observation.result
        return cls(
            run_id=str(observation.run_id),
            request_id=str(observation.request_id),
            query_id=str(observation.query_id),
            model=result.model,
            elapsed_ms=result.elapsed_ms,
            status=result.status,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            attempt_count=result.attempt_count,
            retry_count=result.retry_count,
            raw={
                "run_id": observation.run_id,
                "request_id": observation.request_id,
                "query_id": observation.query_id,
                "result": asdict(result),
            },
        )

    @property
    def token_work(self) -> float:
        prompt = self.prompt_tokens or 0
        completion = self.completion_tokens or 0
        return float(prompt + completion)


@dataclass(frozen=True)
class ResourceSample:
    run_id: str
    timestamp: str
    resource_type: str
    resource_id: str
    cpu_utilization: float | None = None
    memory_bytes: int | None = None
    gpu_utilization: float | None = None
    gpu_memory_used_bytes: int | None = None
    gpu_power_watts: float | None = None
    gpu_temperature_c: float | None = None
    gpu_energy_joules: float | None = None
    network_rx_bytes: int | None = None
    network_tx_bytes: int | None = None
    raw: RawDict = field(default_factory=dict)


@dataclass(frozen=True)
class CostProfile:
    name: str
    version: str
    machine_hourly_usd: float
    cpu_pool_fraction: float = 0.35
    gpu_pool_fraction: float = 0.45
    overhead_pool_fraction: float = 0.20
    cpu_attribution_method: str = "cpu_seconds"
    gpu_attribution_method: str = "shared_token_work"
    prefill_token_weight: float = 1.0
    decode_token_weight: float = 4.0
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.machine_hourly_usd < 0:
            raise ValueError("machine_hourly_usd must be non-negative")
        pool_sum = (
            self.cpu_pool_fraction
            + self.gpu_pool_fraction
            + self.overhead_pool_fraction
        )
        if abs(pool_sum - 1.0) > 1e-9:
            raise ValueError("cost pool fractions must sum to 1.0")

    @property
    def profile_id(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass(frozen=True)
class RequestCost:
    run_id: str
    profile_id: str
    request_id: str
    query_id: str
    success: bool
    total_cost_usd: float
    cpu_cost_usd: float
    gpu_cost_usd: float
    overhead_cost_usd: float
    cpu_seconds: float
    token_work: float
    wall_seconds: float
    raw: RawDict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCost:
    run_id: str
    profile_id: str
    agent: str
    calls: int
    wall_time_ms: float
    cpu_time_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    failures: int
    attributed_cost_usd: float
    cost_percentage: float
    raw: RawDict = field(default_factory=dict)


@dataclass(frozen=True)
class DerivedMetric:
    run_id: str
    name: str
    version: str
    value: Any
    calculated_at: str
    cost_profile_id: str | None = None
    cost_profile_name: str | None = None
    cost_profile_version: str | None = None
    raw: RawDict = field(default_factory=dict)


@dataclass(frozen=True)
class AnalyticsDataset:
    run: ExperimentRun
    requests: tuple[RequestObservation, ...] = ()
    execution_observations: tuple[ExecutionObservation, ...] = ()
    inference_observations: tuple[InferenceObservationRecord, ...] = ()
    resource_samples: tuple[ResourceSample, ...] = ()

    @classmethod
    def from_benchmark_result(
        cls,
        result: Any,
        *,
        execution_observations: list[ExecutionObservation] | None = None,
        inference_observations: list[InferenceObservationRecord] | None = None,
        resource_samples: list[ResourceSample] | None = None,
    ) -> "AnalyticsDataset":
        return cls(
            run=ExperimentRun.from_benchmark_metadata(result.run_metadata),
            requests=tuple(RequestObservation.from_raw(item) for item in result.observations),
            execution_observations=tuple(execution_observations or ()),
            inference_observations=tuple(inference_observations or ()),
            resource_samples=tuple(resource_samples or ()),
        )


@dataclass(frozen=True)
class CostAnalysis:
    run_id: str
    profile: CostProfile
    total_run_cost_usd: float
    request_costs: tuple[RequestCost, ...]
    agent_costs: tuple[AgentCost, ...]
    metrics: tuple[DerivedMetric, ...]

    @property
    def request_cost_sum_usd(self) -> float:
        return sum(item.total_cost_usd for item in self.request_costs)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def distribution(values: list[float]) -> RawDict:
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "stddev": 0.0,
            "iqr": 0.0,
        }
    mean = sum(values) / len(values)
    p25 = percentile(values, 0.25)
    p75 = percentile(values, 0.75)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "median": float(median(values)),
        "p50": percentile(values, 0.50),
        "p75": p75,
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "stddev": sqrt(variance),
        "iqr": p75 - p25,
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
