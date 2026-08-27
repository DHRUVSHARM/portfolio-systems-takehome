"""Configuration for the Phase 5 Gateway benchmark runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class BenchmarkConfig:
    dataset_mode: str = "canonical_100"
    concurrency: int = 10
    gateway_base_url: str = "http://localhost:8000"
    request_timeout_seconds: float = 60.0
    sample_size: int | None = None
    sample_seed: int = 1
    run_id: str | None = None
    run_name: str | None = None
    output_root: Path | str = "results"
    write_artifacts: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_mode, str) or not self.dataset_mode:
            raise ValueError("dataset_mode must be a non-empty string")
        if not isinstance(self.concurrency, int) or self.concurrency <= 0:
            raise ValueError("concurrency must be a positive integer")
        if (
            not isinstance(self.request_timeout_seconds, (int, float))
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_seconds must be positive")
        if self.sample_size is not None and (
            not isinstance(self.sample_size, int) or self.sample_size <= 0
        ):
            raise ValueError("sample_size must be a positive integer when provided")
        if not isinstance(self.sample_seed, int):
            raise ValueError("sample_seed must be an integer")
        if not isinstance(self.gateway_base_url, str) or not self.gateway_base_url:
            raise ValueError("gateway_base_url must be a non-empty string")

    def resolved_run_id(self) -> str:
        return self.run_id or f"run-{uuid4().hex}"

    def output_root_path(self) -> Path:
        return Path(self.output_root)

    def resolved_sample_size(self) -> int | None:
        if self.dataset_mode.startswith("sampled_") and self.dataset_mode != "sampled_N":
            suffix = self.dataset_mode.removeprefix("sampled_")
            if suffix.isdigit():
                return int(suffix)
        return self.sample_size

    def as_resolved_dict(self, *, run_id: str) -> dict:
        return {
            "dataset_mode": self.dataset_mode,
            "concurrency": self.concurrency,
            "gateway_base_url": self.gateway_base_url,
            "request_timeout_seconds": self.request_timeout_seconds,
            "sample_size": self.resolved_sample_size(),
            "sample_seed": self.sample_seed,
            "run_id": run_id,
            "run_name": self.run_name,
            "output_root": str(self.output_root_path()),
            "write_artifacts": self.write_artifacts,
        }
