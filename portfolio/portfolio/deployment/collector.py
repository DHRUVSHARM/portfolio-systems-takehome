"""Post-run collection bridge from benchmark/telemetry artifacts into analytics."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ..analytics.calculators.cost import calculate_costs
from ..analytics.exporters.parquet import write_parquet_run_artifacts
from ..analytics.exporters.postgres import PostgresAnalyticsRepository
from ..analytics.exporters.report import build_run_report
from ..analytics.exporters.visual_report import generate_visual_report
from ..analytics.models import (
    AnalyticsDataset,
    ExecutionObservation,
    ExperimentRun,
    InferenceObservationRecord,
    RequestObservation,
    ResourceSample,
)
from ..analytics.profiles import load_cost_profile
from .provenance import build_run_provenance, redact_secrets
from .telemetry_normalization import resource_type, sample_fields


class TelemetryRequiredError(RuntimeError):
    """Raised when a measured canonical run is missing required telemetry."""


def build_dataset_from_artifacts(
    benchmark_run_dir: Path | str,
    *,
    jaeger_trace_path: Path | str | None = None,
    prometheus_samples_path: Path | str | None = None,
    provenance: dict[str, Any] | None = None,
) -> AnalyticsDataset:
    run_dir = Path(benchmark_run_dir)
    metadata = json.loads((run_dir / "run.json").read_text())
    request_rows = _read_jsonl(run_dir / "requests.jsonl")
    run_id = str(metadata["run_id"])
    query_by_request = {str(row["request_id"]): str(row["query_id"]) for row in request_rows}

    raw_metadata = dict(metadata)
    if provenance:
        raw_metadata["provenance"] = redact_secrets(provenance)
        raw_metadata.setdefault("git_commit", provenance.get("git_commit"))
        raw_metadata.setdefault("config_hashes", provenance.get("config_hashes"))
        raw_metadata.setdefault("hardware_profile", provenance.get("host"))

        inference_profile = provenance.get("inference_profile") or {}
        resolved_inference = inference_profile.get("resolved") or {}

        for key in (
            "model",
            "model_revision",
            "vllm_version",
            "dtype",
            "max_model_len",
            "max_num_seqs",
            "max_num_batched_tokens",
            "gpu_memory_utilization",
            "prefix_caching_enabled",
        ):
            value = resolved_inference.get(key)
            if (
                raw_metadata.get(key) in (None, "")
                and value is not None
            ):
                raw_metadata[key] = value

    traces = _load_json(jaeger_trace_path) if jaeger_trace_path else None
    prometheus = _load_json(prometheus_samples_path) if prometheus_samples_path else None

    execution, inference = _observations_from_jaeger(
        traces, run_id=run_id, query_by_request=query_by_request
    )
    resources = _resource_samples_from_prometheus(prometheus, run_id=run_id)

    return AnalyticsDataset(
        run=ExperimentRun.from_benchmark_metadata(raw_metadata),
        requests=tuple(RequestObservation.from_raw(row) for row in request_rows),
        execution_observations=tuple(execution),
        inference_observations=tuple(inference),
        resource_samples=tuple(resources),
    )


def collect_historical_artifacts(
    benchmark_run_dir: Path | str,
    *,
    cost_profile_path: Path | str,
    output_dir: Path | str,
    postgres_dsn: str | None = None,
    jaeger_trace_path: Path | str | None = None,
    prometheus_samples_path: Path | str | None = None,
    inference_profile_path: Path | str | None = None,
    compose_files: list[str | Path] | None = None,
    require_telemetry: bool = False,
) -> Path:
    run_dir = Path(benchmark_run_dir)
    run_metadata = json.loads((run_dir / "run.json").read_text())
    output_path = Path(output_dir)
    if require_telemetry:
        try:
            _validate_required_telemetry(
                jaeger_trace_path=jaeger_trace_path,
                prometheus_samples_path=prometheus_samples_path,
            )
        except TelemetryRequiredError as exc:
            _write_incomplete_report(
                output_path=output_path,
                run_metadata=run_metadata,
                reason=str(exc),
            )
            raise

    provenance = build_run_provenance(
        run_id=str(run_metadata["run_id"]),
        run_name=run_metadata.get("run_name"),
        compose_files=compose_files,
        cost_profile_path=cost_profile_path,
        inference_profile_path=inference_profile_path,
    )
    dataset = build_dataset_from_artifacts(
        run_dir,
        jaeger_trace_path=jaeger_trace_path,
        prometheus_samples_path=prometheus_samples_path,
        provenance=provenance,
    )
    profile = load_cost_profile(cost_profile_path)
    analysis = calculate_costs(dataset, profile)
    output_path = write_parquet_run_artifacts(
        output_dir=output_path,
        dataset=dataset,
        analysis=analysis,
    )
    _write_json(output_path / "provenance.json", provenance)
    _write_json(output_path / "report.json", build_run_report(dataset, analysis))
    generate_visual_report(output_path)

    if postgres_dsn:
        _persist_to_postgres(
            postgres_dsn=postgres_dsn,
            dataset=dataset,
            analysis=analysis,
            cost_profile_path=cost_profile_path,
        )

    return output_path


def _observations_from_jaeger(
    data: Any,
    *,
    run_id: str,
    query_by_request: dict[str, str],
) -> tuple[list[ExecutionObservation], list[InferenceObservationRecord]]:
    if not data:
        return [], []

    spans = _iter_jaeger_spans(data)

    # Some child agent/tool spans do not repeat request correlation tags.
    # Resolve one request_id per trace and use it as a safe fallback.
    trace_request_ids: dict[str, str] = {}
    for span in spans:
        tags = _span_tags(span)
        request_id = tags.get("request_id") or tags.get(
            "http.request.header.x_request_id"
        )
        if not request_id:
            continue

        trace_id = str(span.get("traceID") or span.get("trace_id") or "")
        if not trace_id:
            continue

        request_id = str(request_id)
        existing = trace_request_ids.get(trace_id)
        if existing is not None and existing != request_id:
            raise ValueError(
                f"trace {trace_id} contains multiple request IDs: "
                f"{existing!r}, {request_id!r}"
            )
        trace_request_ids[trace_id] = request_id

    execution: list[ExecutionObservation] = []
    inference: list[InferenceObservationRecord] = []
    for span in spans:
        tags = _span_tags(span)
        trace_id = str(span.get("traceID") or span.get("trace_id") or "")
        request_id = str(
            tags.get("request_id")
            or tags.get("http.request.header.x_request_id")
            or trace_request_ids.get(trace_id)
            or ""
        )
        if not request_id:
            continue
        query_id = str(tags.get("query_id") or query_by_request.get(request_id) or "")
        stage = str(tags.get("stage") or _stage_from_operation(span.get("operationName", "")))
        agent = str(tags.get("agent") or _agent_from_stage(stage))
        started_at, finished_at, elapsed_ms = _span_times(span)
        observation_id = _span_observation_id(span)
        status = "error" if tags.get("error") else "success"

        if _is_inference_span(span, tags, stage):
            inference.append(
                InferenceObservationRecord(
                    run_id=run_id,
                    request_id=request_id,
                    query_id=query_id,
                    model=str(tags.get("model") or tags.get("llm.model") or "unknown"),
                    elapsed_ms=elapsed_ms,
                    status=int(tags.get("http.status_code") or 0),
                    agent="AdvisorAgent",
                    started_at=started_at,
                    finished_at=finished_at,
                    prompt_tokens=_optional_int(
                        tags.get("llm.prompt_tokens", tags.get("prompt_tokens"))
                    ),
                    completion_tokens=_optional_int(
                        tags.get(
                            "llm.completion_tokens",
                            tags.get("completion_tokens"),
                        )
                    ),
                    total_tokens=_optional_int(
                        tags.get("llm.total_tokens", tags.get("total_tokens"))
                    ),
                    ttft_ms=_optional_float(tags.get("ttft_ms")),
                    queue_ms=_optional_float(tags.get("queue_ms")),
                    prefill_ms=_optional_float(tags.get("prefill_ms")),
                    decode_ms=_optional_float(tags.get("decode_ms")),
                    generation_ms=_optional_float(tags.get("generation_ms")),
                    tokens_per_second=_optional_float(tags.get("tokens_per_second")),
                    error_type=_optional_str(tags.get("error_type")),
                    attempt_count=_optional_int(tags.get("inference.attempt")) or 1,
                    retry_count=_optional_int(tags.get("inference.retry_count")) or 0,
                    raw=redact_secrets(
                        {
                            "source": "jaeger",
                            "observation_id": observation_id,
                            "span_id": span.get("spanID") or span.get("span_id"),
                            "trace_id": span.get("traceID") or span.get("trace_id"),
                            "aggregate_inference_metrics_note": (
                                "Prometheus vLLM histograms are kept as run-level "
                                "resource telemetry unless exact per-request fields "
                                "are present on this span."
                            ),
                        }
                    ),
                )
            )
            continue

        execution.append(
            ExecutionObservation(
                run_id=run_id,
                request_id=request_id,
                query_id=query_id,
                stage=stage,
                agent=agent,
                tool=_optional_str(tags.get("tool")),
                ticker=_optional_str(tags.get("ticker")),
                started_at=started_at,
                finished_at=finished_at,
                observation_id=observation_id,
                parent_observation_id=_parent_span_id(span),
                wall_time_ms=elapsed_ms,
                status=status,
                error_type=_optional_str(tags.get("error_type")),
                raw=redact_secrets({"source": "jaeger", "span": span}),
            )
        )
    return execution, inference


def _validate_required_telemetry(
    *,
    jaeger_trace_path: Path | str | None,
    prometheus_samples_path: Path | str | None,
) -> None:
    if jaeger_trace_path is None:
        raise TelemetryRequiredError("required Jaeger trace JSON was not provided")
    if prometheus_samples_path is None:
        raise TelemetryRequiredError("required Prometheus samples JSON was not provided")

    traces = _load_json(jaeger_trace_path)
    if not _iter_jaeger_spans(traces):
        raise TelemetryRequiredError(
            f"required Jaeger telemetry is empty: {jaeger_trace_path}"
        )

    prometheus = _load_json(prometheus_samples_path)
    rows = (
        prometheus
        if isinstance(prometheus, list)
        else prometheus.get("data", {}).get("result", [])
        if isinstance(prometheus, dict)
        else []
    )
    if not any(row.get("values") for row in rows if isinstance(row, dict)):
        raise TelemetryRequiredError(
            f"required Prometheus telemetry is empty: {prometheus_samples_path}"
        )


def _write_incomplete_report(
    *,
    output_path: Path,
    run_metadata: dict[str, Any],
    reason: str,
) -> None:
    output_path.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_path / "incomplete_report.json",
        {
            "run_id": run_metadata.get("run_id"),
            "status": "incomplete",
            "invalid_reason": reason,
            "raw_benchmark_artifacts_preserved": True,
        },
    )


def _resource_samples_from_prometheus(data: Any, *, run_id: str) -> list[ResourceSample]:
    if not data:
        return []
    rows = data if isinstance(data, list) else data.get("data", {}).get("result", [])
    samples: list[ResourceSample] = []
    for series in rows:
        metric = series.get("metric", {})
        values = series.get("values") or []
        metric_name = metric.get("__name__") or metric.get("name") or "prometheus_sample"
        resource_id = metric.get("instance") or metric.get("pod") or metric.get("container") or metric_name
        for timestamp, value in values:
            sample_value = _optional_float(value)
            fields = _sample_fields(str(metric_name), sample_value)
            samples.append(
                ResourceSample(
                    run_id=run_id,
                    timestamp=_epoch_to_iso(float(timestamp)),
                    resource_type=_resource_type(str(metric_name), metric),
                    resource_id=str(resource_id),
                    raw=redact_secrets(
                        {
                            "source": "prometheus_range_query",
                            "metric": metric,
                            "value": sample_value,
                        }
                    ),
                    **fields,
                )
            )
    return samples


def _persist_to_postgres(
    *,
    postgres_dsn: str,
    dataset: AnalyticsDataset,
    analysis: Any,
    cost_profile_path: Path | str,
) -> None:
    import psycopg

    profile = load_cost_profile(cost_profile_path)
    with psycopg.connect(postgres_dsn) as connection:
        repository = PostgresAnalyticsRepository(connection)
        repository.apply_schema()
        repository.persist_raw_dataset(dataset)
        repository.persist_metric_registry()
        repository.persist_cost_profile(profile)
        repository.persist_cost_analysis(analysis)


def _iter_jaeger_spans(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "data" in data:
        traces = data["data"]
    elif isinstance(data, list):
        traces = data
    else:
        traces = [data]
    spans: list[dict[str, Any]] = []
    for trace in traces:
        if isinstance(trace, dict) and isinstance(trace.get("spans"), list):
            spans.extend(trace["spans"])
        elif isinstance(trace, dict) and "spanID" in trace:
            spans.append(trace)
    return spans


def _span_tags(span: dict[str, Any]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for item in span.get("tags", []):
        if isinstance(item, dict) and "key" in item:
            tags[str(item["key"])] = item.get("value")
    attributes = span.get("attributes")
    if isinstance(attributes, dict):
        tags.update(attributes)
    return tags


def _is_inference_span(
    span: dict[str, Any],
    tags: dict[str, Any],
    stage: str,
) -> bool:
    operation = str(span.get("operationName") or span.get("name") or "")
    return (
        operation == "inference.request"
        or stage == "inference"
        or (
            "inference.attempt" in tags
            and "inference" in operation.lower()
        )
    )


def _span_times(span: dict[str, Any]) -> tuple[str, str, float]:
    if "startTime" in span:
        start_us = float(span["startTime"])
        duration_us = float(span.get("duration") or 0.0)
        return (
            _epoch_to_iso(start_us / 1_000_000.0),
            _epoch_to_iso((start_us + duration_us) / 1_000_000.0),
            duration_us / 1000.0,
        )
    start = str(span.get("start_time") or span.get("startTimeUnixNano") or "")
    end = str(span.get("end_time") or span.get("endTimeUnixNano") or "")
    return start, end, 0.0


def _span_observation_id(span: dict[str, Any]) -> str:
    trace_id = span.get("traceID") or span.get("trace_id") or "unknown-trace"
    span_id = span.get("spanID") or span.get("span_id") or "unknown-span"
    return f"otel:{trace_id}:{span_id}"


def _parent_span_id(span: dict[str, Any]) -> str | None:
    for reference in span.get("references", []):
        if reference.get("refType") == "CHILD_OF":
            trace_id = reference.get("traceID") or span.get("traceID")
            return f"otel:{trace_id}:{reference.get('spanID')}"
    parent = span.get("parent_span_id")
    if parent:
        return f"otel:{span.get('trace_id', 'unknown-trace')}:{parent}"
    return None


def _stage_from_operation(name: str) -> str:
    lowered = name.lower()
    for stage in ("gateway", "portfolio", "metrics", "price", "risk", "advisor", "inference"):
        if stage in lowered:
            return stage
    return "unknown"


def _agent_from_stage(stage: str) -> str:
    return {
        "metrics": "MetricsAgent",
        "price": "PriceAgent",
        "risk": "RiskAgent",
        "advisor": "AdvisorAgent",
        "inference": "AdvisorAgent",
    }.get(stage, stage.title() if stage else "unknown")


def _sample_fields(metric_name: str, value: float | None) -> dict[str, Any]:
    return sample_fields(metric_name, value)


def _resource_type(metric_name: str, metric: dict[str, Any]) -> str:
    return resource_type(metric_name, metric)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as file:
        return [json.loads(line) for line in file if line.strip()]


def _load_json(path: Path | str | None) -> Any:
    if path is None:
        return None
    return json.loads(Path(path).read_text())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")


def _epoch_to_iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Phase 8 benchmark telemetry into Phase 7 analytics artifacts"
    )
    parser.add_argument("--benchmark-run-dir", required=True, type=Path)
    parser.add_argument("--cost-profile", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--jaeger-trace-json", type=Path)
    parser.add_argument("--prometheus-samples-json", type=Path)
    parser.add_argument("--inference-profile", type=Path)
    parser.add_argument("--compose-file", action="append", default=[])
    parser.add_argument("--require-telemetry", action="store_true")
    args = parser.parse_args()

    output = collect_historical_artifacts(
        args.benchmark_run_dir,
        cost_profile_path=args.cost_profile,
        output_dir=args.output_dir,
        postgres_dsn=args.postgres_dsn,
        jaeger_trace_path=args.jaeger_trace_json,
        prometheus_samples_path=args.prometheus_samples_json,
        inference_profile_path=args.inference_profile,
        compose_files=args.compose_file,
        require_telemetry=args.require_telemetry,
    )
    print(output)


if __name__ == "__main__":
    main()
