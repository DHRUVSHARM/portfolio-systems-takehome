"""PostgreSQL schema and persistence helpers for historical observations."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from ..models import AnalyticsDataset, CostAnalysis, CostProfile
from ..registry import registry_as_rows


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def load_schema_sql() -> str:
    return SCHEMA_PATH.read_text()


class PostgresAnalyticsRepository:
    """Small DB-API adapter that keeps raw observations authoritative."""

    def __init__(self, connection: Any):
        self.connection = connection

    def apply_schema(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(load_schema_sql())
        self.connection.commit()

    def persist_raw_dataset(self, dataset: AnalyticsDataset) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO experiment_runs (
                  run_id, started_at, finished_at, duration_seconds, status,
                  invalid_reason, run_name, dataset_mode, selected_query_count,
                  selection_manifest, sample_seed, benchmark_concurrency,
                  gateway_max_in_flight, gateway_queue_capacity,
                  workflow_cpu_workers, max_concurrent_metric_tasks, model,
                  model_revision, vllm_version, dtype, max_model_len,
                  max_num_seqs, max_num_batched_tokens, gpu_memory_utilization,
                  prefix_caching_enabled, hardware_profile, git_commit,
                  config_hashes, cost_profile_name, cost_profile_version, raw
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s
                )
                ON CONFLICT (run_id) DO UPDATE SET
                  finished_at = EXCLUDED.finished_at,
                  duration_seconds = EXCLUDED.duration_seconds,
                  status = EXCLUDED.status,
                  invalid_reason = EXCLUDED.invalid_reason,
                  raw = EXCLUDED.raw
                """,
                (
                    dataset.run.run_id,
                    dataset.run.started_at,
                    dataset.run.finished_at,
                    dataset.run.duration_seconds,
                    dataset.run.status,
                    dataset.run.invalid_reason,
                    dataset.run.run_name,
                    dataset.run.dataset_mode,
                    dataset.run.selected_query_count,
                    _json(dataset.run.selection_manifest),
                    dataset.run.sample_seed,
                    dataset.run.benchmark_concurrency,
                    dataset.run.gateway_max_in_flight,
                    dataset.run.gateway_queue_capacity,
                    dataset.run.workflow_cpu_workers,
                    dataset.run.max_concurrent_metric_tasks,
                    dataset.run.model,
                    dataset.run.model_revision,
                    dataset.run.vllm_version,
                    dataset.run.dtype,
                    dataset.run.max_model_len,
                    dataset.run.max_num_seqs,
                    dataset.run.max_num_batched_tokens,
                    dataset.run.gpu_memory_utilization,
                    dataset.run.prefix_caching_enabled,
                    _json(dataset.run.hardware_profile),
                    dataset.run.git_commit,
                    _json(dataset.run.config_hashes),
                    dataset.run.cost_profile_name,
                    dataset.run.cost_profile_version,
                    _json(dataset.run.raw),
                ),
            )
            for request in dataset.requests:
                cursor.execute(
                    """
                    INSERT INTO requests (
                      run_id, request_id, query_id, n_holdings, phrasing,
                      lookback_days, start_timestamp, finish_timestamp,
                      client_latency_ms, gateway_latency_ms, gateway_queue_wait_ms,
                      http_status, success, error_type, response_body, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO UPDATE SET
                      finish_timestamp = EXCLUDED.finish_timestamp,
                      client_latency_ms = EXCLUDED.client_latency_ms,
                      http_status = EXCLUDED.http_status,
                      success = EXCLUDED.success,
                      error_type = EXCLUDED.error_type,
                      response_body = EXCLUDED.response_body,
                      raw = EXCLUDED.raw
                    """,
                    (
                        request.run_id,
                        request.request_id,
                        request.query_id,
                        request.n_holdings,
                        request.phrasing,
                        request.lookback_days,
                        request.start_timestamp,
                        request.finish_timestamp,
                        request.client_latency_ms,
                        request.gateway_latency_ms,
                        request.gateway_queue_wait_ms,
                        request.http_status,
                        request.success,
                        request.error_type,
                        _json(request.response_body),
                        _json(request.raw),
                    ),
                )
            for index, observation in enumerate(dataset.execution_observations):
                observation_id = observation.observation_id or (
                    f"{observation.run_id}:{observation.request_id}:"
                    f"{observation.stage}:{observation.agent}:"
                    f"{observation.tool or 'stage'}:{observation.ticker or 'all'}:{index}"
                )
                cursor.execute(
                    """
                    INSERT INTO execution_observations (
                      observation_id, parent_observation_id, run_id, request_id,
                      query_id, stage, agent, tool, ticker, started_at, finished_at,
                      wall_time_ms, cpu_time_ms, status, error_type, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (observation_id) DO UPDATE SET
                      parent_observation_id = EXCLUDED.parent_observation_id,
                      finished_at = EXCLUDED.finished_at,
                      wall_time_ms = EXCLUDED.wall_time_ms,
                      cpu_time_ms = EXCLUDED.cpu_time_ms,
                      status = EXCLUDED.status,
                      error_type = EXCLUDED.error_type,
                      raw = EXCLUDED.raw
                    """,
                    (
                        observation_id,
                        observation.parent_observation_id,
                        observation.run_id,
                        observation.request_id,
                        observation.query_id,
                        observation.stage,
                        observation.agent,
                        observation.tool,
                        observation.ticker,
                        observation.started_at,
                        observation.finished_at,
                        observation.wall_time_ms,
                        observation.cpu_time_ms,
                        observation.status,
                        observation.error_type,
                        _json(observation.raw),
                    ),
                )
            for index, observation in enumerate(dataset.inference_observations):
                observation_key = _inference_observation_key(observation, index)
                cursor.execute(
                    """
                    INSERT INTO inference_observations (
                      observation_key, run_id, request_id, query_id, agent, model,
                      started_at, finished_at, elapsed_ms, prompt_tokens,
                      completion_tokens, total_tokens, ttft_ms, queue_ms, prefill_ms,
                      decode_ms, generation_ms, mean_itl_ms, tpot_ms,
                      tokens_per_second, status, error_type, attempt_count,
                      retry_count, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (observation_key) DO UPDATE SET
                      finished_at = EXCLUDED.finished_at,
                      elapsed_ms = EXCLUDED.elapsed_ms,
                      prompt_tokens = EXCLUDED.prompt_tokens,
                      completion_tokens = EXCLUDED.completion_tokens,
                      total_tokens = EXCLUDED.total_tokens,
                      ttft_ms = EXCLUDED.ttft_ms,
                      queue_ms = EXCLUDED.queue_ms,
                      prefill_ms = EXCLUDED.prefill_ms,
                      decode_ms = EXCLUDED.decode_ms,
                      generation_ms = EXCLUDED.generation_ms,
                      mean_itl_ms = EXCLUDED.mean_itl_ms,
                      tpot_ms = EXCLUDED.tpot_ms,
                      tokens_per_second = EXCLUDED.tokens_per_second,
                      status = EXCLUDED.status,
                      error_type = EXCLUDED.error_type,
                      attempt_count = EXCLUDED.attempt_count,
                      retry_count = EXCLUDED.retry_count,
                      raw = EXCLUDED.raw
                    """,
                    (
                        observation_key,
                        observation.run_id,
                        observation.request_id,
                        observation.query_id,
                        observation.agent,
                        observation.model,
                        observation.started_at,
                        observation.finished_at,
                        observation.elapsed_ms,
                        observation.prompt_tokens,
                        observation.completion_tokens,
                        observation.total_tokens,
                        observation.ttft_ms,
                        observation.queue_ms,
                        observation.prefill_ms,
                        observation.decode_ms,
                        observation.generation_ms,
                        observation.mean_itl_ms,
                        observation.tpot_ms,
                        observation.tokens_per_second,
                        observation.status,
                        observation.error_type,
                        observation.attempt_count,
                        observation.retry_count,
                        _json(observation.raw),
                    ),
                )
            for sample in dataset.resource_samples:
                cursor.execute(
                    """
                    INSERT INTO resource_samples (
                      run_id, timestamp, resource_type, resource_id,
                      cpu_utilization, memory_bytes, gpu_utilization,
                      gpu_memory_used_bytes, gpu_power_watts, gpu_temperature_c,
                      gpu_energy_joules, network_rx_bytes, network_tx_bytes, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, timestamp, resource_type, resource_id)
                    DO UPDATE SET
                      cpu_utilization = EXCLUDED.cpu_utilization,
                      memory_bytes = EXCLUDED.memory_bytes,
                      gpu_utilization = EXCLUDED.gpu_utilization,
                      gpu_memory_used_bytes = EXCLUDED.gpu_memory_used_bytes,
                      gpu_power_watts = EXCLUDED.gpu_power_watts,
                      gpu_temperature_c = EXCLUDED.gpu_temperature_c,
                      gpu_energy_joules = EXCLUDED.gpu_energy_joules,
                      network_rx_bytes = EXCLUDED.network_rx_bytes,
                      network_tx_bytes = EXCLUDED.network_tx_bytes,
                      raw = EXCLUDED.raw
                    """,
                    (
                        sample.run_id,
                        sample.timestamp,
                        sample.resource_type,
                        sample.resource_id,
                        sample.cpu_utilization,
                        sample.memory_bytes,
                        sample.gpu_utilization,
                        sample.gpu_memory_used_bytes,
                        sample.gpu_power_watts,
                        sample.gpu_temperature_c,
                        sample.gpu_energy_joules,
                        sample.network_rx_bytes,
                        sample.network_tx_bytes,
                        _json(sample.raw),
                    ),
                )
        self.connection.commit()

    def persist_cost_profile(self, profile: CostProfile) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cost_profiles (
                  profile_id, name, version, machine_hourly_usd,
                  cpu_pool_fraction, gpu_pool_fraction, overhead_pool_fraction,
                  cpu_attribution_method, gpu_attribution_method,
                  prefill_token_weight, decode_token_weight, notes, raw
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (profile_id) DO UPDATE SET raw = EXCLUDED.raw
                """,
                (
                    profile.profile_id,
                    profile.name,
                    profile.version,
                    profile.machine_hourly_usd,
                    profile.cpu_pool_fraction,
                    profile.gpu_pool_fraction,
                    profile.overhead_pool_fraction,
                    profile.cpu_attribution_method,
                    profile.gpu_attribution_method,
                    profile.prefill_token_weight,
                    profile.decode_token_weight,
                    profile.notes,
                    _json(asdict(profile)),
                ),
            )
        self.connection.commit()

    def persist_metric_registry(self) -> None:
        with self.connection.cursor() as cursor:
            for row in registry_as_rows():
                cursor.execute(
                    """
                    INSERT INTO metric_registry (
                      metric_name, version, description, formula, inputs
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (metric_name, version) DO UPDATE SET
                      description = EXCLUDED.description,
                      formula = EXCLUDED.formula,
                      inputs = EXCLUDED.inputs
                    """,
                    (
                        row["name"],
                        row["version"],
                        row["description"],
                        row["formula"],
                        _json(row["inputs"]),
                    ),
                )
        self.connection.commit()

    def persist_cost_analysis(self, analysis: CostAnalysis) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cost_analyses (
                  run_id, profile_id, profile_name, profile_version,
                  total_run_cost_usd, request_cost_sum_usd, raw
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, profile_id) DO UPDATE SET
                  profile_name = EXCLUDED.profile_name,
                  profile_version = EXCLUDED.profile_version,
                  generated_at = now(),
                  total_run_cost_usd = EXCLUDED.total_run_cost_usd,
                  request_cost_sum_usd = EXCLUDED.request_cost_sum_usd,
                  raw = EXCLUDED.raw
                """,
                (
                    analysis.run_id,
                    analysis.profile.profile_id,
                    analysis.profile.name,
                    analysis.profile.version,
                    analysis.total_run_cost_usd,
                    analysis.request_cost_sum_usd,
                    _json({"metric_count": len(analysis.metrics)}),
                ),
            )
            for row in analysis.request_costs:
                cursor.execute(
                    """
                    INSERT INTO request_cost_attributions (
                      run_id, profile_id, request_id, query_id, success, total_cost_usd,
                      cpu_cost_usd, gpu_cost_usd, overhead_cost_usd, cpu_seconds,
                      token_work, wall_seconds, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, profile_id, request_id) DO UPDATE SET
                      query_id = EXCLUDED.query_id,
                      success = EXCLUDED.success,
                      total_cost_usd = EXCLUDED.total_cost_usd,
                      cpu_cost_usd = EXCLUDED.cpu_cost_usd,
                      gpu_cost_usd = EXCLUDED.gpu_cost_usd,
                      overhead_cost_usd = EXCLUDED.overhead_cost_usd,
                      cpu_seconds = EXCLUDED.cpu_seconds,
                      token_work = EXCLUDED.token_work,
                      wall_seconds = EXCLUDED.wall_seconds,
                      raw = EXCLUDED.raw
                    """,
                    (
                        row.run_id,
                        row.profile_id,
                        row.request_id,
                        row.query_id,
                        row.success,
                        row.total_cost_usd,
                        row.cpu_cost_usd,
                        row.gpu_cost_usd,
                        row.overhead_cost_usd,
                        row.cpu_seconds,
                        row.token_work,
                        row.wall_seconds,
                        _json(row.raw),
                    ),
                )
            for row in analysis.agent_costs:
                cursor.execute(
                    """
                    INSERT INTO agent_cost_attributions (
                      run_id, profile_id, agent, calls, wall_time_ms, cpu_time_ms,
                      p50_latency_ms, p95_latency_ms, p99_latency_ms, failures,
                      attributed_cost_usd, cost_percentage, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, profile_id, agent) DO UPDATE SET
                      calls = EXCLUDED.calls,
                      wall_time_ms = EXCLUDED.wall_time_ms,
                      cpu_time_ms = EXCLUDED.cpu_time_ms,
                      p50_latency_ms = EXCLUDED.p50_latency_ms,
                      p95_latency_ms = EXCLUDED.p95_latency_ms,
                      p99_latency_ms = EXCLUDED.p99_latency_ms,
                      failures = EXCLUDED.failures,
                      attributed_cost_usd = EXCLUDED.attributed_cost_usd,
                      cost_percentage = EXCLUDED.cost_percentage,
                      raw = EXCLUDED.raw
                    """,
                    (
                        row.run_id,
                        row.profile_id,
                        row.agent,
                        row.calls,
                        row.wall_time_ms,
                        row.cpu_time_ms,
                        row.p50_latency_ms,
                        row.p95_latency_ms,
                        row.p99_latency_ms,
                        row.failures,
                        row.attributed_cost_usd,
                        row.cost_percentage,
                        _json(row.raw),
                    ),
                )
            for row in analysis.metrics:
                cursor.execute(
                    """
                    INSERT INTO derived_metrics (
                      run_id, profile_id, metric_name, metric_version, calculated_at,
                      cost_profile_name, cost_profile_version, value, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, profile_id, metric_name, metric_version)
                    DO UPDATE SET
                      calculated_at = EXCLUDED.calculated_at,
                      cost_profile_name = EXCLUDED.cost_profile_name,
                      cost_profile_version = EXCLUDED.cost_profile_version,
                      value = EXCLUDED.value,
                      raw = EXCLUDED.raw
                    """,
                    (
                        row.run_id,
                        row.cost_profile_id,
                        row.name,
                        row.version,
                        row.calculated_at,
                        row.cost_profile_name,
                        row.cost_profile_version,
                        _json(row.value),
                        _json(row.raw),
                    ),
                )
        self.connection.commit()


def _json(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return json.dumps(value, sort_keys=True)
    return Jsonb(value)


def _inference_observation_key(observation: Any, index: int) -> str:
    raw = observation.raw or {}
    explicit_id = raw.get("observation_id") or raw.get("inference_id")
    if explicit_id is not None:
        return f"{observation.run_id}:{explicit_id}"
    return "|".join(
        str(part)
        for part in (
            observation.run_id,
            observation.request_id,
            observation.query_id,
            observation.agent,
            observation.model,
            observation.started_at or "",
            observation.finished_at or "",
            observation.attempt_count,
            index,
        )
    )
