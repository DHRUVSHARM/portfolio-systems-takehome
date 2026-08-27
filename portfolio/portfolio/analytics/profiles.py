"""Versioned cost profile loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CostProfile


def load_cost_profile(path: Path | str) -> CostProfile:
    data = _load_simple_yaml(Path(path).read_text())
    return CostProfile(
        name=str(data["name"]),
        version=str(data["version"]),
        machine_hourly_usd=float(data["machine_hourly_usd"]),
        cpu_pool_fraction=float(data.get("cpu_pool_fraction", 0.35)),
        gpu_pool_fraction=float(data.get("gpu_pool_fraction", 0.45)),
        overhead_pool_fraction=float(data.get("overhead_pool_fraction", 0.20)),
        cpu_attribution_method=str(data.get("cpu_attribution_method", "cpu_seconds")),
        gpu_attribution_method=str(
            data.get("gpu_attribution_method", "shared_token_work")
        ),
        prefill_token_weight=float(data.get("prefill_token_weight", 1.0)),
        decode_token_weight=float(data.get("decode_token_weight", 4.0)),
        notes=data.get("notes"),
    )


def _load_simple_yaml(text: str) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    current_key: str | None = None
    current_block: list[str] = []

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith(" ") and current_key is not None:
            current_block.append(raw_line.strip())
            continue
        if current_key is not None:
            rows[current_key] = " ".join(current_block).strip()
            current_key = None
            current_block = []

        key, separator, value = raw_line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if value == ">":
            current_key = key
            current_block = []
        else:
            rows[key] = _parse_scalar(value)

    if current_key is not None:
        rows[current_key] = " ".join(current_block).strip()

    return rows


def _parse_scalar(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
