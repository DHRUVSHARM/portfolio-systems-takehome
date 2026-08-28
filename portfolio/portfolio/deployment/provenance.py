"""Run provenance helpers for reproducible Phase 8 experiments."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import Any


SECRET_MARKERS = ("token", "password", "secret", "credential", "api_key", "apikey")


def build_run_provenance(
    *,
    run_id: str,
    run_name: str | None = None,
    compose_files: list[str | Path] | None = None,
    benchmark_config_path: str | Path | None = None,
    cost_profile_path: str | Path | None = None,
    inference_profile_path: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture non-secret local provenance without inventing unavailable fields."""

    files = [Path(path) for path in (compose_files or [])]
    for optional in (benchmark_config_path, cost_profile_path, inference_profile_path):
        if optional:
            files.append(Path(optional))

    provenance = {
        "run_id": run_id,
        "run_name": run_name,
        "git_commit": _git_sha(),
        "host": _host_profile(),
        "config_hashes": {
            str(path): _hash_file(path) for path in files if path.exists()
        },
        "environment": redact_secrets(
            {
                key: value
                for key, value in os.environ.items()
                if _is_relevant_env_key(key)
            }
        ),
    }
    if inference_profile_path:
        profile_path = Path(inference_profile_path)
        if profile_path.exists():
            provenance["inference_profile"] = {
                "path": str(profile_path),
                "sha256": _hash_file(profile_path),
            }
    if cost_profile_path:
        profile_path = Path(cost_profile_path)
        if profile_path.exists():
            provenance["cost_profile"] = {
                "path": str(profile_path),
                "sha256": _hash_file(profile_path),
            }
    if extra:
        provenance.update(redact_secrets(extra))
    return provenance


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if _looks_secret(str(key)):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_secrets(nested)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _host_profile() -> dict[str, Any]:
    profile: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
    }
    gpu = _nvidia_smi()
    if gpu:
        profile["nvidia_smi"] = gpu
    return profile


def _nvidia_smi() -> dict[str, str] | None:
    query = "driver_version,cuda_version,name,memory.total"
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    line = next((row.strip() for row in result.stdout.splitlines() if row.strip()), "")
    if not line:
        return None
    parts = [part.strip() for part in line.split(",")]
    keys = ["driver_version", "cuda_version", "name", "memory_total_mb"]
    return dict(zip(keys, parts))


def _is_relevant_env_key(key: str) -> bool:
    return bool(
        re.match(
            r"^(GATEWAY_|PORTFOLIO_|VLLM_|BENCHMARK_|OTEL_|OBSERVABILITY_|JSON_|POSTGRES_)",
            key,
        )
    )


def _looks_secret(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in SECRET_MARKERS)
