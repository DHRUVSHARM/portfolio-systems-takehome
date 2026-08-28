"""Historical serving telemetry interpretation for collected runs."""

from __future__ import annotations

import json
import math
from statistics import mean
from typing import Any

from .models import distribution


VLLM_METRICS = {
    "vllm:num_requests_running": ("Requests running", "requests", "gauge", 1.0),
    "vllm:num_requests_waiting": ("Requests waiting", "requests", "gauge", 1.0),
    "vllm:prompt_tokens_total": ("Prompt token throughput", "tokens/s", "rate", 1.0),
    "vllm:generation_tokens_total": ("Generation token throughput", "tokens/s", "rate", 1.0),
    "vllm:e2e_p50": ("Aggregate E2E p50", "ms", "run-level", 1000.0),
    "vllm:e2e_p95": ("Aggregate E2E p95", "ms", "run-level", 1000.0),
    "vllm:ttft_p50": ("Aggregate TTFT p50", "ms", "run-level", 1000.0),
    "vllm:ttft_p95": ("Aggregate TTFT p95", "ms", "run-level", 1000.0),
    "vllm:tpot_p50": ("Aggregate TPOT p50", "ms", "run-level", 1000.0),
    "vllm:tpot_p95": ("Aggregate TPOT p95", "ms", "run-level", 1000.0),
    "vllm:queue_p95": ("Aggregate queue p95", "ms", "run-level", 1000.0),
    "vllm:prefill_p95": ("Aggregate prefill p95", "ms", "run-level", 1000.0),
    "vllm:decode_p95": ("Aggregate decode p95", "ms", "run-level", 1000.0),
    "vllm:gpu_cache_usage_perc": ("KV-cache utilization", "percent", "gauge", 100.0),
    "vllm:num_preemptions_total": ("Preemptions", "count", "increase", 1.0),
    "vllm:prefix_cache_hits_total": ("Prefix-cache hits", "hits/s", "rate", 1.0),
    "vllm:prefix_cache_queries_total": ("Prefix-cache queries", "queries/s", "rate", 1.0),
}

GPU_RAW_METRICS = {
    "gpu_utilization": {
        "dcgm_fi_dev_gpu_util": 1.0,
    },
    "gpu_memory_used_bytes": {
        "dcgm_fi_dev_fb_used": 1024.0 * 1024.0,
    },
    "gpu_power_watts": {
        "dcgm_fi_dev_power_usage": 1.0,
    },
    "gpu_temperature_c": {
        "dcgm_fi_dev_gpu_temp": 1.0,
    },
    "gpu_energy_joules": {
        "dcgm_fi_dev_total_energy_consumption": 1.0 / 1000.0,
    },
}


def summarize_serving_telemetry(
    *,
    resource_samples: list[Any],
    inference_observations: list[Any],
) -> dict[str, Any]:
    inference_rows = [_row(item) for item in inference_observations]
    resource_rows = [_row(item) for item in resource_samples]
    elapsed = [_float(row.get("elapsed_ms")) for row in inference_rows]
    prompt_tokens = [
        value for value in (_float(row.get("prompt_tokens")) for row in inference_rows)
        if value is not None
    ]
    completion_tokens = [
        value
        for value in (_float(row.get("completion_tokens")) for row in inference_rows)
        if value is not None
    ]
    total_tokens = [
        value for value in (_float(row.get("total_tokens")) for row in inference_rows)
        if value is not None
    ]
    prometheus = _prometheus_values_by_name(resource_rows)

    return _sanitize(
        {
            "source_note": (
                "Exact per-request inference values come from inference spans. "
                "vLLM scheduler and histogram values are aggregate run-level "
                "Prometheus telemetry and are not assigned to individual requests."
            ),
            "inference": {
                "request_count": len(inference_rows),
                "total_prompt_tokens": int(sum(prompt_tokens)),
                "total_completion_tokens": int(sum(completion_tokens)),
                "total_tokens": int(sum(total_tokens)),
                "elapsed_ms": distribution(
                    [value for value in elapsed if value is not None]
                ),
            },
            "vllm": {
                name: _metric_summary(
                    name=name,
                    label=label,
                    unit=unit,
                    source=source,
                    scale=scale,
                    values=prometheus.get(name, []),
                )
                for name, (label, unit, source, scale) in VLLM_METRICS.items()
            },
            "gpu": {
                "utilization_percent": _field_summary(
                    resource_rows, "gpu_utilization", "GPU utilization", "percent"
                ),
                "memory_used_bytes": _field_summary(
                    resource_rows, "gpu_memory_used_bytes", "GPU memory used", "bytes"
                ),
                "power_watts": _field_summary(resource_rows, "gpu_power_watts", "GPU power", "watts"),
                "temperature_c": _field_summary(
                    resource_rows, "gpu_temperature_c", "GPU temperature", "celsius"
                ),
                "energy_joules": _field_summary(
                    resource_rows, "gpu_energy_joules", "GPU energy", "joules"
                ),
            },
        }
    )


def _prometheus_values_by_name(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        raw = _parse_raw(row.get("raw"))
        metric = raw.get("metric") if isinstance(raw, dict) else None
        if not isinstance(metric, dict):
            continue
        name = metric.get("name") or metric.get("__name__")
        if not name or metric.get("availability") == "unavailable":
            continue
        value = _float(raw.get("value"))
        if value is not None:
            grouped.setdefault(str(name), []).append(value)
    return grouped


def _metric_summary(
    *,
    name: str,
    label: str,
    unit: str,
    source: str,
    scale: float,
    values: list[float],
) -> dict[str, Any]:
    if not values:
        return {
            "name": name,
            "label": label,
            "unit": unit,
            "source": source,
            "availability": "unavailable",
            "latest": None,
            "mean": None,
            "max": None,
        }
    scaled = [_scale_percent_if_needed(name, value * scale) for value in values]
    return {
        "name": name,
        "label": label,
        "unit": unit,
        "source": source,
        "availability": "available",
        "latest": scaled[-1],
        "mean": mean(scaled),
        "max": max(scaled),
    }


def _field_summary(
    rows: list[dict[str, Any]],
    field: str,
    label: str,
    unit: str,
) -> dict[str, Any]:
    values = [_float(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    values.extend(_raw_gpu_values(rows, field))
    if not values:
        return {
            "label": label,
            "unit": unit,
            "availability": "unavailable",
            "mean": None,
            "max": None,
            "latest": None,
        }
    normalized = [_as_percent(value) if unit == "percent" else value for value in values]
    return {
        "label": label,
        "unit": unit,
        "availability": "available",
        "mean": mean(normalized),
        "max": max(normalized),
        "latest": normalized[-1],
    }


def _raw_gpu_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    metrics = GPU_RAW_METRICS.get(field, {})
    values: list[float] = []
    for row in rows:
        if _float(row.get(field)) is not None:
            continue
        raw = _parse_raw(row.get("raw"))
        if not isinstance(raw, dict):
            continue
        metric = raw.get("metric")
        if not isinstance(metric, dict) or metric.get("availability") == "unavailable":
            continue
        name = str(metric.get("name") or metric.get("__name__") or "").lower()
        scale = metrics.get(name)
        if scale is None:
            continue
        value = _float(raw.get("value"))
        if value is not None:
            values.append(value * scale)
    return values


def _scale_percent_if_needed(name: str, value: float) -> float:
    if name.endswith("gpu_cache_usage_perc"):
        return _as_percent(value)
    return value


def _as_percent(value: float) -> float:
    return value * 100.0 if 0.0 <= value <= 1.0 else value


def _row(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    return dict(item)


def _parse_raw(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
