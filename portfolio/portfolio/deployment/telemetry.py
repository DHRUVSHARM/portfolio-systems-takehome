"""Post-run telemetry export helpers for Jaeger and Prometheus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


DEFAULT_TRACE_SERVICES = ("gateway", "portfolio-api")
DEFAULT_PROMETHEUS_QUERIES = {
    "gateway_inflight_requests": "gateway_inflight_requests",
    "gateway_queued_requests": "gateway_queued_requests",
    "portfolio_workflow_cpu_slots_used": "portfolio_workflow_cpu_slots_used",
    "portfolio_workflow_cpu_slots_waiting": "portfolio_workflow_cpu_slots_waiting",
    "portfolio_metric_tasks_running": "portfolio_metric_tasks_running",
    "container_cpu_usage_seconds_total": (
        'rate(container_cpu_usage_seconds_total{container!="",image!=""}[1m])'
    ),
    "container_memory_working_set_bytes": (
        'container_memory_working_set_bytes{container!="",image!=""}'
    ),
    "node_cpu_seconds_total": 'rate(node_cpu_seconds_total{mode!="idle"}[1m])',
    "node_memory_MemAvailable_bytes": "node_memory_MemAvailable_bytes",
    "DCGM_FI_DEV_GPU_UTIL": "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_FB_USED": "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_POWER_USAGE": "DCGM_FI_DEV_POWER_USAGE",
}
DEFAULT_VLLM_QUERIES = {
    "vllm:num_requests_running": "vllm:num_requests_running",
    "vllm:num_requests_waiting": "vllm:num_requests_waiting",
    "vllm:gpu_cache_usage_perc": "vllm:gpu_cache_usage_perc",
    "vllm:e2e_request_latency_seconds": "vllm:e2e_request_latency_seconds",
    "vllm:time_to_first_token_seconds": "vllm:time_to_first_token_seconds",
    "vllm:time_per_output_token_seconds": "vllm:time_per_output_token_seconds",
    "vllm:request_prompt_tokens": "vllm:request_prompt_tokens",
    "vllm:request_generation_tokens": "vllm:request_generation_tokens",
}


def export_jaeger_traces(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    output_path: Path | str,
    jaeger_base_url: str = "http://localhost:16686",
    services: tuple[str, ...] = DEFAULT_TRACE_SERVICES,
) -> Path:
    traces: dict[str, dict[str, Any]] = {}
    with httpx.Client(base_url=jaeger_base_url.rstrip("/"), timeout=30.0) as client:
        for service in services:
            response = client.get(
                "/api/traces",
                params={
                    "service": service,
                    "start": _iso_to_epoch_micros(started_at),
                    "end": _iso_to_epoch_micros(finished_at),
                    "tags": json.dumps({"run_id": run_id}),
                    "limit": 10000,
                },
            )
            response.raise_for_status()
            for trace in response.json().get("data", []):
                trace_id = str(trace.get("traceID") or trace.get("trace_id"))
                traces[trace_id] = trace

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, {"data": list(traces.values())})
    return output


def export_prometheus_range(
    *,
    started_at: str,
    finished_at: str,
    output_path: Path | str,
    prometheus_base_url: str = "http://localhost:9090",
    step: str = "15s",
    include_gpu: bool = False,
    include_vllm: bool = False,
) -> Path:
    queries = dict(DEFAULT_PROMETHEUS_QUERIES)
    if not include_gpu:
        queries = {
            name: query
            for name, query in queries.items()
            if not name.startswith("DCGM_")
        }
    if include_vllm:
        queries.update(DEFAULT_VLLM_QUERIES)

    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=prometheus_base_url.rstrip("/"), timeout=30.0) as client:
        for name, query in queries.items():
            response = client.get(
                "/api/v1/query_range",
                params={
                    "query": query,
                    "start": started_at,
                    "end": finished_at,
                    "step": step,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") == "success":
                for row in payload.get("data", {}).get("result", []):
                    row.setdefault("metric", {})["name"] = name
                    results.append(row)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, {"data": {"result": results}})
    return output


def _iso_to_epoch_micros(value: str) -> int:
    from datetime import datetime

    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1_000_000)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export post-run Jaeger and Prometheus telemetry JSON"
    )
    parser.add_argument("--run-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--jaeger-base-url", default="http://localhost:16686")
    parser.add_argument("--prometheus-base-url", default="http://localhost:9090")
    parser.add_argument("--prometheus-step", default="15s")
    parser.add_argument("--include-gpu", action="store_true")
    parser.add_argument("--include-vllm", action="store_true")
    args = parser.parse_args()

    run = json.loads(args.run_json.read_text())
    run_id = str(run["run_id"])
    trace_path = export_jaeger_traces(
        run_id=run_id,
        started_at=str(run["started_at"]),
        finished_at=str(run["finished_at"]),
        output_path=args.output_dir / f"{run_id}_jaeger_traces.json",
        jaeger_base_url=args.jaeger_base_url,
    )
    prometheus_path = export_prometheus_range(
        started_at=str(run["started_at"]),
        finished_at=str(run["finished_at"]),
        output_path=args.output_dir / f"{run_id}_prometheus_samples.json",
        prometheus_base_url=args.prometheus_base_url,
        step=args.prometheus_step,
        include_gpu=args.include_gpu,
        include_vllm=args.include_vllm,
    )
    print(json.dumps({"jaeger_trace_json": str(trace_path), "prometheus_samples_json": str(prometheus_path)}))


if __name__ == "__main__":
    main()
