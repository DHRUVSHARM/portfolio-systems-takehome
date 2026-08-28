"""Post-run telemetry export helpers for Jaeger and Prometheus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
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
    "vllm:num_preemptions_total": "increase(vllm:num_preemptions_total[1m])",
    "vllm:prompt_tokens_total": "rate(vllm:prompt_tokens_total[1m])",
    "vllm:generation_tokens_total": "rate(vllm:generation_tokens_total[1m])",
    "vllm:prefix_cache_hits_total": "rate(vllm:prefix_cache_hits_total[1m])",
    "vllm:prefix_cache_queries_total": "rate(vllm:prefix_cache_queries_total[1m])",
    "vllm:e2e_p50": (
        "histogram_quantile(0.50, sum(rate(vllm:e2e_request_latency_seconds_bucket[1m])) by (le))"
    ),
    "vllm:e2e_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[1m])) by (le))"
    ),
    "vllm:ttft_p50": (
        "histogram_quantile(0.50, sum(rate(vllm:time_to_first_token_seconds_bucket[1m])) by (le))"
    ),
    "vllm:ttft_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:time_to_first_token_seconds_bucket[1m])) by (le))"
    ),
    "vllm:tpot_p50": (
        "histogram_quantile(0.50, sum(rate(vllm:time_per_output_token_seconds_bucket[1m])) by (le))"
    ),
    "vllm:tpot_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:time_per_output_token_seconds_bucket[1m])) by (le))"
    ),
    "vllm:queue_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:request_queue_time_seconds_bucket[1m])) by (le))"
    ),
    "vllm:prefill_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:request_prefill_time_seconds_bucket[1m])) by (le))"
    ),
    "vllm:decode_p95": (
        "histogram_quantile(0.95, sum(rate(vllm:request_decode_time_seconds_bucket[1m])) by (le))"
    ),
}
VLLM_EXPECTED_METRICS = {
    "vllm:num_requests_running": ("vllm:num_requests_running",),
    "vllm:num_requests_waiting": ("vllm:num_requests_waiting",),
    "vllm:gpu_cache_usage_perc": ("vllm:gpu_cache_usage_perc",),
    "vllm:num_preemptions_total": ("vllm:num_preemptions_total",),
    "vllm:prompt_tokens_total": ("vllm:prompt_tokens_total", "vllm:prompt_tokens"),
    "vllm:generation_tokens_total": ("vllm:generation_tokens_total", "vllm:generation_tokens"),
    "vllm:prefix_cache_hits_total": ("vllm:prefix_cache_hits_total",),
    "vllm:prefix_cache_queries_total": ("vllm:prefix_cache_queries_total",),
    "vllm:e2e_request_latency_seconds_bucket": ("vllm:e2e_request_latency_seconds_bucket",),
    "vllm:time_to_first_token_seconds_bucket": ("vllm:time_to_first_token_seconds_bucket",),
    "vllm:time_per_output_token_seconds_bucket": ("vllm:time_per_output_token_seconds_bucket",),
    "vllm:request_queue_time_seconds_bucket": ("vllm:request_queue_time_seconds_bucket",),
    "vllm:request_prefill_time_seconds_bucket": ("vllm:request_prefill_time_seconds_bucket",),
    "vllm:request_decode_time_seconds_bucket": ("vllm:request_decode_time_seconds_bucket",),
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
                query_rows = payload.get("data", {}).get("result", [])
                if not query_rows:
                    results.append(
                        {
                            "metric": {
                                "name": name,
                                "query": query,
                                "availability": "unavailable",
                            },
                            "values": [],
                        }
                    )
                    continue
                for row in query_rows:
                    row.setdefault("metric", {})["name"] = name
                    row.setdefault("metric", {})["query"] = query
                    row.setdefault("metric", {})["availability"] = "available"
                    results.append(row)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, {"data": {"result": results}})
    return output


def inspect_vllm_metrics_text(text: str) -> dict[str, dict[str, str | None]]:
    available = _metric_names(text)
    report: dict[str, dict[str, str | None]] = {}
    for expected, aliases in VLLM_EXPECTED_METRICS.items():
        matched = next((name for name in aliases if name in available), None)
        report[expected] = {
            "status": "available" if matched == expected else "alternate" if matched else "unavailable",
            "matched_name": matched,
        }
    return report


def preflight_vllm_metrics(
    *,
    vllm_base_url: str = "http://localhost:8000",
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    with httpx.Client(base_url=vllm_base_url.rstrip("/"), timeout=15.0) as client:
        response = client.get("/metrics")
        response.raise_for_status()
    report = {
        "vllm_base_url": vllm_base_url,
        "metrics": inspect_vllm_metrics_text(response.text),
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output, report)
    return report


def _metric_names(text: str) -> set[str]:
    names: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)", line)
        if match:
            names.add(match.group(1))
    return names


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
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("vllm-preflight")
    preflight.add_argument("--vllm-base-url", default="http://localhost:8000")
    preflight.add_argument("--output", type=Path)

    export = subparsers.add_parser("export-run")
    export.add_argument("--run-json", required=True, type=Path)
    export.add_argument("--output-dir", required=True, type=Path)
    export.add_argument("--jaeger-base-url", default="http://localhost:16686")
    export.add_argument("--prometheus-base-url", default="http://localhost:9090")
    export.add_argument("--prometheus-step", default="15s")
    export.add_argument("--include-gpu", action="store_true")
    export.add_argument("--include-vllm", action="store_true")
    args = parser.parse_args()

    if args.command == "vllm-preflight":
        print(
            json.dumps(
                preflight_vllm_metrics(
                    vllm_base_url=args.vllm_base_url,
                    output_path=args.output,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return

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
