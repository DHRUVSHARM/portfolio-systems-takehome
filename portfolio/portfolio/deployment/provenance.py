"""Run provenance helpers for reproducible Phase 8 experiments."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ..analytics.profiles import load_cost_profile


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
            profile_data = yaml.safe_load(profile_path.read_text()) or {}
            vllm_profile = profile_data.get("vllm") or {}

            def env_override(name: str, default: Any = None) -> Any:
                value = os.environ.get(name)
                return value if value not in (None, "") else default

            prefix_env = os.environ.get("VLLM_ENABLE_PREFIX_CACHING")
            if prefix_env not in (None, ""):
                # Compose enables the flag whenever this variable is non-empty.
                prefix_caching_enabled = True
            else:
                prefix_caching_enabled = vllm_profile.get(
                    "prefix_caching_enabled"
                )

            provenance["inference_profile"] = {
                "path": str(profile_path),
                "sha256": _hash_file(profile_path),
                "resolved": {
                    "model": env_override(
                        "VLLM_MODEL",
                        profile_data.get("model"),
                    ),
                    "model_revision": env_override(
                        "VLLM_MODEL_REVISION",
                        profile_data.get("model_revision"),
                    ),
                    "vllm_version": vllm_profile.get("vllm_version"),
                    "dtype": env_override(
                        "VLLM_DTYPE",
                        vllm_profile.get("dtype"),
                    ),
                    "max_model_len": env_override(
                        "VLLM_MAX_MODEL_LEN",
                        vllm_profile.get("max_model_len"),
                    ),
                    "max_num_seqs": env_override(
                        "VLLM_MAX_NUM_SEQS",
                        vllm_profile.get("max_num_seqs"),
                    ),
                    "max_num_batched_tokens": env_override(
                        "VLLM_MAX_NUM_BATCHED_TOKENS",
                        vllm_profile.get("max_num_batched_tokens"),
                    ),
                    "gpu_memory_utilization": env_override(
                        "VLLM_GPU_MEMORY_UTILIZATION",
                        vllm_profile.get("gpu_memory_utilization"),
                    ),
                    "prefix_caching_enabled": prefix_caching_enabled,
                },
            }
    if cost_profile_path:
        profile_path = Path(cost_profile_path)
        if profile_path.exists():
            profile = load_cost_profile(profile_path)
            provenance["cost_profile"] = {
                "path": str(profile_path),
                "sha256": _hash_file(profile_path),
                "name": profile.name,
                "version": profile.version,
                "profile_id": profile.profile_id,
                "machine_hourly_usd": profile.machine_hourly_usd,
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
    profile["gpu"] = gpu or {
        "name": None,
        "memory_total_mb": None,
        "driver_version": None,
        "driver_supported_cuda_version": None,
        "toolkit_cuda_version": None,
    }
    return profile


def _nvidia_smi() -> dict[str, str] | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
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
    gpu = parse_nvidia_smi_gpu_query(line)
    gpu["driver_supported_cuda_version"] = _driver_cuda_version()
    gpu["toolkit_cuda_version"] = _toolkit_cuda_version()
    return gpu


def parse_nvidia_smi_gpu_query(line: str) -> dict[str, str | None]:
    parts = [part.strip() for part in line.split(",")]
    name = parts[0] if len(parts) > 0 else None
    memory = parts[1] if len(parts) > 1 else None
    driver = parts[2] if len(parts) > 2 else None
    return {
        "name": name or None,
        "memory_total_mb": memory or None,
        "driver_version": driver or None,
        "driver_supported_cuda_version": None,
        "toolkit_cuda_version": None,
    }


def parse_driver_cuda_version(nvidia_smi_output: str) -> str | None:
    match = re.search(r"CUDA Version:\s*([0-9.]+)", nvidia_smi_output)
    return match.group(1) if match else None


def _driver_cuda_version() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return parse_driver_cuda_version(result.stdout)


def _toolkit_cuda_version() -> str | None:
    try:
        result = subprocess.run(
            ["nvcc", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"release\s+([0-9.]+)", result.stdout)
    return match.group(1) if match else None


def _is_relevant_env_key(key: str) -> bool:
    return bool(
        re.match(
            r"^(GATEWAY_|PORTFOLIO_|VLLM_|BENCHMARK_|OTEL_|OBSERVABILITY_|JSON_|POSTGRES_|PROVIDER_|MACHINE_HOURLY_USD)",
            key,
        )
    )


def _looks_secret(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in SECRET_MARKERS)
