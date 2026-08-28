"""Prometheus/DCGM resource telemetry normalization."""

from __future__ import annotations

from typing import Any


def sample_fields(metric_name: str, value: float | None) -> dict[str, Any]:
    if value is None:
        return {}
    normalized = metric_name.lower()
    if normalized == "dcgm_fi_dev_gpu_util":
        return {"gpu_utilization": value}
    if normalized == "dcgm_fi_dev_fb_used":
        return {"gpu_memory_used_bytes": int(value * 1024 * 1024)}
    if normalized == "dcgm_fi_dev_power_usage":
        return {"gpu_power_watts": value}
    if normalized == "dcgm_fi_dev_gpu_temp":
        return {"gpu_temperature_c": value}
    if normalized == "dcgm_fi_dev_total_energy_consumption":
        return {"gpu_energy_joules": value / 1000.0}
    if "cpu" in normalized:
        return {"cpu_utilization": value}
    if "memory" in normalized or "mem" in normalized:
        return {"memory_bytes": int(value)}
    if "gpu" in normalized and "util" in normalized:
        return {"gpu_utilization": value}
    if "fb_used" in normalized or "gpu_memory" in normalized:
        return {"gpu_memory_used_bytes": int(value)}
    if "power" in normalized:
        return {"gpu_power_watts": value}
    if "temperature" in normalized or "gpu_temp" in normalized:
        return {"gpu_temperature_c": value}
    if "energy" in normalized:
        return {"gpu_energy_joules": value}
    if "network_receive" in normalized or "network_rx" in normalized:
        return {"network_rx_bytes": int(value)}
    if "network_transmit" in normalized or "network_tx" in normalized:
        return {"network_tx_bytes": int(value)}
    return {}


def resource_type(metric_name: str, metric: dict[str, Any]) -> str:
    normalized = metric_name.lower()
    service = str(metric.get("service") or metric.get("job") or "").lower()
    if "dcgm" in normalized or "gpu" in normalized or service == "dcgm-exporter":
        return "gpu"
    if "container" in normalized or service == "cadvisor":
        return "container"
    if service == "node-exporter" or normalized.startswith("node_"):
        return "host"
    return "prometheus"
