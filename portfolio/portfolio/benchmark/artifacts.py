"""Durable Phase 5 benchmark artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_run_artifacts(
    *,
    output_root: Path,
    run_id: str,
    run_metadata: dict[str, Any],
    resolved_config: dict[str, Any],
    observations: list[Any],
) -> Path:
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    _write_json(run_dir / "run.json", run_metadata)
    _write_yaml(run_dir / "resolved_benchmark_config.yaml", resolved_config)
    with (run_dir / "requests.jsonl").open("w") as file:
        for observation in observations:
            row = observation.to_json_dict()
            file.write(json.dumps(row, sort_keys=True) + "\n")

    return run_dir


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as file:
        _write_yaml_mapping(file, data)


def _write_yaml_mapping(file, data: dict[str, Any], indent: int = 0) -> None:
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            file.write(f"{prefix}{key}:\n")
            _write_yaml_mapping(file, value, indent + 2)
        elif isinstance(value, list):
            file.write(f"{prefix}{key}:\n")
            for item in value:
                file.write(f"{prefix}  - {_yaml_scalar(item)}\n")
        else:
            file.write(f"{prefix}{key}: {_yaml_scalar(value)}\n")


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'
