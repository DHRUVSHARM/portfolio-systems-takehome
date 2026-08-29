"""Configuration for the serving-safe portfolio runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowRuntimeConfig:
    cpu_workers: int = 8
    max_concurrent_metric_tasks: int = 16

    def __post_init__(self) -> None:
        _validate_positive_int("cpu_workers", self.cpu_workers)
        _validate_positive_int(
            "max_concurrent_metric_tasks", self.max_concurrent_metric_tasks
        )


def _validate_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
