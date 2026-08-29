"""Static HTML/SVG visual report generation for collected analytics runs."""

from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from ..models import distribution, percentile
from ..serving import summarize_serving_telemetry
from .parquet import read_parquet_rows
from .report import build_report_from_artifacts


CHART_TITLES = {
    "cost_query_histogram": ("Cost / Query Histogram", "Allocated USD by request."),
    "cost_query_boxplot": ("Cost / Query Box Plot", "Median, quartiles and range."),
    "cost_query_cdf": ("Cost / Query CDF", "Cumulative fraction by query cost."),
    "latency_distribution": ("Latency Distribution", "Client-observed latency in ms."),
    "cost_latency_by_holdings": ("Cost vs Latency by Holdings", "Request cost and latency grouped by workload size."),
    "query_type_breakdown": ("Query Type Breakdown", "Workload phrasing mix."),
    "holdings_count_breakdown": ("Holdings Count Breakdown", "Portfolio-size mix and latency."),
    "cost_vs_tokens": ("Cost vs Tokens", "Allocated cost against LLM token volume."),
    "agent_cost_share": ("Agent Cost Share", "Total infrastructure cost attribution."),
    "agent_tool_latency": ("Agent / Tool Latency", "Observed agent/tool wall time."),
    "fanout_work": ("Workflow Fan-Out Work", "Cumulative work vs critical path."),
}


def generate_visual_report(run_dir: Path | str) -> Path:
    root = Path(run_dir)
    report_dir = root / "report"
    assets_dir = report_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    bundle = _load_bundle(root)
    chart_assets = _render_chart_assets(root / "charts", assets_dir)
    timeline = _render_timeline_asset(bundle, assets_dir)
    if timeline:
        chart_assets["request_timeline"] = timeline
    serving = summarize_serving_telemetry(
        resource_samples=bundle["resource_samples"],
        inference_observations=bundle["inference_observations"],
    )
    _write_json(root / "serving_telemetry.json", serving)
    _write_json(assets_dir / "chart_manifest.json", chart_assets)
    index = report_dir / "index.html"
    index.write_text(_render_html(bundle, chart_assets, serving), encoding="utf-8")
    return index


def _load_bundle(root: Path) -> dict[str, Any]:
    run = _read_json(root / "run.json")
    report = _read_json(root / "report.json") if (root / "report.json").exists() else build_report_from_artifacts(root)
    provenance = _read_json(root / "provenance.json") if (root / "provenance.json").exists() else {}
    metrics = _read_json(root / "metrics.json") if (root / "metrics.json").exists() else {"metrics": []}
    return {
        "root": root,
        "run": run,
        "report": report,
        "provenance": provenance,
        "metrics": metrics,
        "requests": _read_rows(root / "requests.parquet"),
        "execution_observations": _read_rows(root / "execution_observations.parquet"),
        "inference_observations": _read_rows(root / "inference_observations.parquet"),
        "resource_samples": _read_rows(root / "resource_samples.parquet"),
        "request_costs": _read_rows(root / "request_cost_attributions.parquet"),
        "agent_costs": _read_rows(root / "agent_cost_attributions.parquet"),
    }


def _render_chart_assets(charts_dir: Path, assets_dir: Path) -> dict[str, str]:
    assets: dict[str, str] = {}
    if not charts_dir.exists():
        return assets
    for path in sorted(charts_dir.glob("*.json")):
        name = path.stem
        data = _read_json(path)
        title, subtitle = CHART_TITLES.get(name, (name.replace("_", " ").title(), ""))
        target = assets_dir / f"{name}.svg"
        target.write_text(_render_chart(name, data, title, subtitle), encoding="utf-8")
        assets[name] = f"assets/{target.name}"
    return assets


def _render_chart(name: str, data: dict[str, Any], title: str, subtitle: str) -> str:
    if name == "cost_query_histogram":
        return _bar_chart(title, subtitle, [(f"{_fmt_usd(row['lower'])}-{_fmt_usd(row['upper'])}", row["count"]) for row in data.get("bins", [])], y_label="Requests")
    if name == "cost_query_boxplot":
        return _boxplot(title, subtitle, [_num(value) for value in data.get("values", [])])
    if name == "cost_query_cdf":
        return _line_chart(title, subtitle, [(row.get("cost_usd"), row.get("cdf")) for row in data.get("points", [])], x_label="Cost (USD)", y_label="Cumulative fraction")
    if name == "latency_distribution":
        return _histogram_values(title, subtitle, [_num(value) for value in data.get("values_ms", [])], x_label="Latency (ms)")
    if name == "cost_latency_by_holdings":
        return _scatter_chart(title, subtitle, [(row.get("latency_ms"), row.get("cost_usd"), str(row.get("n_holdings"))) for row in data.get("points", [])], x_label="Latency (ms)", y_label="Cost (USD)")
    if name in {"query_type_breakdown", "holdings_count_breakdown"}:
        return _bar_chart(title, subtitle, [(str(row.get("group")), row.get("count"), f"{_fmt_ms(row.get('mean_latency_ms'))} mean") for row in data.get("groups", [])], y_label="Requests")
    if name == "cost_vs_tokens":
        return _scatter_chart(title, subtitle, [(_sum_optional(row.get("prompt_tokens"), row.get("completion_tokens")), row.get("cost_usd"), str(row.get("query_id"))) for row in data.get("points", [])], x_label="Tokens", y_label="Cost (USD)")
    if name == "agent_cost_share":
        return _bar_chart(title, subtitle, [(row.get("agent"), row.get("cost_percentage"), _fmt_usd(row.get("cost_usd"))) for row in data.get("agents", [])], y_label="Cost share (%)")
    if name == "agent_tool_latency":
        return _bar_chart(title, subtitle, [(_agent_tool_label(row), (row.get("latency_distribution_ms") or {}).get("p95"), f"{row.get('calls', 0)} calls") for row in data.get("groups", [])], y_label="p95 latency (ms)")
    if name == "fanout_work":
        return _bar_chart(title, subtitle, [(f"{row.get('stage')} cumulative", row.get("cumulative_wall_time_ms"), f"critical {_fmt_ms(row.get('critical_path_wall_time_ms'))}") for row in data.get("groups", [])[:12]], y_label="Wall time (ms)")
    return _empty_chart(title, subtitle)


def _render_html(bundle: dict[str, Any], chart_assets: dict[str, str], serving: dict[str, Any]) -> str:
    run = bundle["run"]
    report = bundle["report"]
    provenance = bundle["provenance"]
    metrics = {row["name"]: row["value"] for row in bundle["metrics"].get("metrics", [])}
    cost_dist = metrics.get("cost_per_query_distribution_usd", {})
    cost_profile = _cost_profile_details(report, provenance)
    requests = bundle["requests"]
    request_costs = {row["request_id"]: row for row in bundle["request_costs"]}
    inference = {row["request_id"]: row for row in bundle["inference_observations"]}
    sections = [
        _overview_section(run, report, cost_profile),
        _assignment_metrics_section(report, metrics, cost_dist),
        _chart_section("Cost", ["cost_query_histogram", "cost_query_boxplot", "cost_query_cdf", "agent_cost_share"], chart_assets),
        _chart_section("Performance", ["latency_distribution", "cost_latency_by_holdings", "cost_vs_tokens"], chart_assets),
        _chart_section("Agents & Tools", ["agent_tool_latency", "fanout_work"], chart_assets),
        _serving_section(serving),
        _chart_section("Workload", ["query_type_breakdown", "holdings_count_breakdown"], chart_assets),
        _request_drilldown_section(requests, request_costs, inference, bundle["execution_observations"], chart_assets),
        _provenance_section(run, provenance),
        _artifact_index_section(bundle["root"], chart_assets),
    ]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>Portfolio Analytics Report - {escape(str(run.get('run_id', 'run')))}</title>",
            f"<style>{_css()}</style>",
            "</head>",
            "<body>",
            "<header>",
            "<h1>Portfolio Analytics Report</h1>",
            f"<p>{escape(str(run.get('run_id', report.get('run_id', 'unknown run'))))}</p>",
            "</header>",
            _nav(),
            "<main>",
            *sections,
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _overview_section(run: dict[str, Any], report: dict[str, Any], cost_profile: dict[str, Any]) -> str:
    warning = '<div class="notice">NONCANONICAL / ILLUSTRATIVE COST ONLY</div>' if _is_illustrative_cost(cost_profile) else ""
    return _section(
        "overview",
        "Overview",
        warning
        + _definition_grid(
            [
                ("Run ID", run.get("run_id")),
                ("Dataset mode", run.get("dataset_mode") or report.get("dataset_mode")),
                ("Query count", report.get("request_count")),
                ("Success count", report.get("success_count")),
                ("Failure count", report.get("failure_count")),
                ("Success rate", _success_rate(report)),
                ("Benchmark concurrency", run.get("benchmark_concurrency")),
                ("Duration", _fmt_seconds(run.get("duration_seconds"))),
                ("Model", run.get("model")),
                ("Model revision", run.get("model_revision")),
                ("vLLM version", run.get("vllm_version")),
                ("dtype", run.get("dtype")),
                ("max model length", run.get("max_model_len")),
                ("max num seqs", run.get("max_num_seqs")),
                ("max batched tokens", run.get("max_num_batched_tokens")),
                ("GPU memory target", _fmt_percent(run.get("gpu_memory_utilization"))),
                ("Prefix caching", _yes_no(run.get("prefix_caching_enabled"))),
                ("Cost profile", cost_profile.get("profile_id")),
                ("Machine hourly cost", _fmt_usd(cost_profile.get("machine_hourly_usd"))),
                ("Cost profile type", cost_profile.get("profile_type")),
            ]
        ),
    )


def _assignment_metrics_section(report: dict[str, Any], metrics: dict[str, Any], cost_dist: dict[str, Any]) -> str:
    agent_pct = metrics.get("agent_cost_percentage", {})
    agent_rows = "".join(f"<tr><td>{escape(str(agent))}</td><td>{_fmt_percent(percent)}</td></tr>" for agent, percent in sorted(agent_pct.items()))
    return _section(
        "assignment-metrics",
        "Assignment Metrics",
        _definition_grid(
            [
                ("Total run cost", _fmt_usd(metrics.get("total_infrastructure_cost_usd", report.get("total_run_cost_usd")))),
                ("Mean cost/query", _fmt_usd(cost_dist.get("mean"))),
                ("Median cost/query", _fmt_usd(cost_dist.get("median"))),
                ("p95 cost/query", _fmt_usd(cost_dist.get("p95"))),
                ("Queries per dollar", _fmt_number(metrics.get("queries_per_dollar"))),
                ("Tokens per dollar", _fmt_number(metrics.get("tokens_per_dollar"))),
                ("Request cost sum", _fmt_usd(report.get("request_cost_sum_usd"))),
            ]
        )
        + "<h3>Percentage of total cost by agent</h3>"
        + f"<table><thead><tr><th>Agent</th><th>Share</th></tr></thead><tbody>{agent_rows}</tbody></table>"
        + '<p class="note">Request cost sum should match total run cost within floating-point tolerance. Failed attempts retain allocated infrastructure cost.</p>',
    )


def _chart_section(title: str, names: list[str], assets: dict[str, str]) -> str:
    cards = []
    for name in names:
        if name in assets:
            label, subtitle = CHART_TITLES.get(name, (name, ""))
            cards.append(f'<figure><img src="{escape(assets[name])}" alt="{escape(label)}"><figcaption>{escape(subtitle)}</figcaption></figure>')
    if not cards:
        cards.append('<p class="empty">N/A / telemetry unavailable for this run</p>')
    return _section(_slug(title), title, '<div class="chart-grid">' + "".join(cards) + "</div>")


def _serving_section(serving: dict[str, Any]) -> str:
    inference = serving["inference"]
    vllm_rows = "".join(
        f"<tr><td>{escape(row['label'])}</td><td>{_fmt_value(row.get('latest'), row.get('unit'))}</td><td>{_fmt_value(row.get('mean'), row.get('unit'))}</td><td>{_fmt_value(row.get('max'), row.get('unit'))}</td><td>{escape(row.get('source') or '')}</td></tr>"
        for row in serving["vllm"].values()
    )
    gpu_rows = "".join(
        f"<tr><td>{escape(row['label'])}</td><td>{_fmt_value(row.get('latest'), row.get('unit'))}</td><td>{_fmt_value(row.get('mean'), row.get('unit'))}</td><td>{_fmt_value(row.get('max'), row.get('unit'))}</td></tr>"
        for row in serving["gpu"].values()
    )
    return _section(
        "inference-serving",
        "Inference & Serving",
        _definition_grid(
            [
                ("Inference request count", inference.get("request_count")),
                ("Total prompt tokens", inference.get("total_prompt_tokens")),
                ("Total completion tokens", inference.get("total_completion_tokens")),
                ("Total tokens", inference.get("total_tokens")),
                ("Inference elapsed p50", _fmt_ms(inference.get("elapsed_ms", {}).get("p50"))),
                ("Inference elapsed p95", _fmt_ms(inference.get("elapsed_ms", {}).get("p95"))),
            ]
        )
        + "<h3>Aggregate vLLM telemetry</h3>"
        + f"<table><thead><tr><th>Metric</th><th>Latest</th><th>Mean</th><th>Max</th><th>Source</th></tr></thead><tbody>{vllm_rows}</tbody></table>"
        + "<h3>GPU telemetry</h3>"
        + f"<table><thead><tr><th>Metric</th><th>Latest</th><th>Mean</th><th>Max</th></tr></thead><tbody>{gpu_rows}</tbody></table>"
        + f"<p class=\"note\">{escape(serving['source_note'])}</p>",
    )


def _request_drilldown_section(
    requests: list[dict[str, Any]],
    request_costs: dict[str, dict[str, Any]],
    inference: dict[str, dict[str, Any]],
    execution: list[dict[str, Any]],
    chart_assets: dict[str, str],
) -> str:
    rows = []
    for request in requests:
        cost = request_costs.get(request["request_id"], {})
        infer = inference.get(request["request_id"], {})
        rows.append(
            "<tr>"
            f"<td>{escape(str(request.get('query_id')))}</td><td>{escape(str(request.get('request_id')))}</td>"
            f"<td>{escape(str(request.get('n_holdings')))}</td><td>{escape(str(request.get('phrasing')))}</td>"
            f"<td>{_fmt_ms(request.get('client_latency_ms'))}</td><td>{_fmt_value(infer.get('prompt_tokens'), 'tokens')}</td>"
            f"<td>{_fmt_value(infer.get('completion_tokens'), 'tokens')}</td><td>{_fmt_usd(cost.get('total_cost_usd'))}</td>"
            f"<td>{escape(str(request.get('success')))}</td></tr>"
        )
    detail = ""
    if requests:
        selected = requests[0]["request_id"]
        exec_rows = [row for row in execution if row.get("request_id") == selected]
        detail_rows = "".join(
            f"<tr><td>{escape(str(row.get('observation_id') or ''))}</td><td>{escape(str(row.get('stage')))}</td><td>{escape(str(row.get('agent')))}</td><td>{escape(str(row.get('tool') or ''))}</td><td>{escape(str(row.get('ticker') or ''))}</td><td>{_fmt_ms(row.get('wall_time_ms'))}</td><td>{escape(str(row.get('parent_observation_id') or ''))}</td></tr>"
            for row in exec_rows
        )
        timeline = f'<figure><img src="{escape(chart_assets["request_timeline"])}" alt="Request execution timeline"></figure>' if "request_timeline" in chart_assets else '<p class="empty">N/A / telemetry unavailable for this run</p>'
        detail = f"<h3>Execution timeline for {escape(str(selected))}</h3>{timeline}<table><thead><tr><th>Observation</th><th>Stage</th><th>Agent</th><th>Tool</th><th>Ticker</th><th>Wall time</th><th>Parent observation</th></tr></thead><tbody>{detail_rows}</tbody></table>"
    return _section(
        "request-drilldown",
        "Request Drilldown",
        "<table><thead><tr><th>Query ID</th><th>Request ID</th><th>Holdings</th><th>Type</th><th>Latency</th><th>Prompt tokens</th><th>Completion tokens</th><th>Cost</th><th>Success</th></tr></thead>"
        + f"<tbody>{''.join(rows)}</tbody></table>"
        + detail,
    )


def _provenance_section(run: dict[str, Any], provenance: dict[str, Any]) -> str:
    resolved = (provenance.get("inference_profile") or {}).get("resolved") or {}
    host = provenance.get("host") or run.get("hardware_profile") or {}
    return _section(
        "provenance",
        "Provenance",
        _definition_grid(
            [
                ("Git SHA", provenance.get("git_commit") or run.get("git_commit")),
                ("Model", resolved.get("model") or run.get("model")),
                ("Model revision", resolved.get("model_revision") or run.get("model_revision")),
                ("vLLM version", resolved.get("vllm_version") or run.get("vllm_version")),
                ("dtype", resolved.get("dtype") or run.get("dtype")),
                ("max model length", resolved.get("max_model_len") or run.get("max_model_len")),
                ("max num seqs", resolved.get("max_num_seqs") or run.get("max_num_seqs")),
                ("max batched tokens", resolved.get("max_num_batched_tokens") or run.get("max_num_batched_tokens")),
                ("Prefix caching", _yes_no(resolved.get("prefix_caching_enabled", run.get("prefix_caching_enabled")))),
                ("Benchmark concurrency", run.get("benchmark_concurrency")),
                ("Dataset mode", run.get("dataset_mode")),
                ("Selected query count", run.get("selected_query_count")),
                ("Hardware", json.dumps(host, sort_keys=True)[:240] if host else None),
            ]
        ),
    )


def _artifact_index_section(root: Path, chart_assets: dict[str, str]) -> str:
    artifacts = [
        "run.json",
        "provenance.json",
        "report.json",
        "metrics.json",
        "serving_telemetry.json",
        "summary.csv",
        "requests.parquet",
        "execution_observations.parquet",
        "inference_observations.parquet",
        "resource_samples.parquet",
        "request_cost_attributions.parquet",
        "agent_cost_attributions.parquet",
    ]
    rows = "".join(
        f'<tr><td>{escape(name)}</td><td><a href="../{escape(name)}">../{escape(name)}</a></td></tr>'
        for name in artifacts
        if (root / name).exists()
    )
    chart_rows = "".join(
        f'<tr><td>{escape(name)}</td><td><a href="{escape(path)}">{escape(path)}</a></td></tr>'
        for name, path in sorted(chart_assets.items())
    )
    return _section("artifact-index", "Artifact Index", f"<table><thead><tr><th>Artifact</th><th>Path</th></tr></thead><tbody>{rows}{chart_rows}</tbody></table>")


def _render_timeline_asset(bundle: dict[str, Any], assets_dir: Path) -> str | None:
    requests = bundle["requests"]
    if not requests:
        return None
    request_id = requests[0]["request_id"]
    rows = [row for row in bundle["execution_observations"] if row.get("request_id") == request_id]
    rows += [
        {**row, "stage": "inference", "agent": "inference.request", "tool": "", "wall_time_ms": row.get("elapsed_ms")}
        for row in bundle["inference_observations"]
        if row.get("request_id") == request_id
    ]
    timed = [row for row in rows if row.get("started_at") and row.get("finished_at")]
    if not timed:
        return None
    start = min(_parse_ts(row["started_at"]) for row in timed)
    end = max(_parse_ts(row["finished_at"]) for row in timed)
    span_ms = max((end - start).total_seconds() * 1000.0, 1.0)
    width, row_h = 920, 30
    height = 90 + len(timed) * row_h
    lines = [_svg_header(width, height), _svg_title("Request Execution Timeline", f"request_id={request_id}", width)]
    y = 70
    for row in timed:
        row_start = _parse_ts(row["started_at"])
        row_end = _parse_ts(row["finished_at"])
        x = 220 + ((row_start - start).total_seconds() * 1000.0 / span_ms) * 660
        bar_w = max(((row_end - row_start).total_seconds() * 1000.0 / span_ms) * 660, 2)
        label = f"{row.get('agent') or ''} {row.get('tool') or ''} {row.get('ticker') or ''}".strip()
        lines.append(f'<text x="24" y="{y + 16}" class="label">{escape(label[:36])}</text>')
        lines.append(f'<rect x="{x:.1f}" y="{y}" width="{bar_w:.1f}" height="18" rx="3" class="bar"></rect>')
        lines.append(f'<text x="{min(x + bar_w + 6, width - 120):.1f}" y="{y + 14}" class="tiny">{_fmt_ms(row.get("wall_time_ms"))}</text>')
        y += row_h
    lines.append("</svg>")
    target = assets_dir / f"request_timeline_{_safe_filename(request_id)}.svg"
    target.write_text("\n".join(lines), encoding="utf-8")
    return f"assets/{target.name}"


def _bar_chart(title: str, subtitle: str, rows: list[tuple[Any, ...]], *, y_label: str) -> str:
    clean = [(str(row[0]), _num(row[1]), str(row[2]) if len(row) > 2 else "") for row in rows]
    clean = [(label, value, note) for label, value, note in clean if value is not None]
    if not clean:
        return _empty_chart(title, subtitle)
    if len(clean) > 18:
        subtitle = f"{subtitle} Showing first 18 of {len(clean)} rows."
    width = 920
    height = max(260, 110 + min(len(clean), 18) * 28)
    max_value = max(value for _label, value, _note in clean) or 1.0
    lines = [_svg_header(width, height), _svg_title(title, subtitle, width), f'<text x="24" y="92" class="axis">{escape(y_label)}</text>']
    y = 110
    for label, value, note in clean[:18]:
        bar_w = (value / max_value) * 560
        lines.append(f'<text x="24" y="{y + 15}" class="label">{escape(label[:28])}</text>')
        lines.append(f'<rect x="240" y="{y}" width="{bar_w:.1f}" height="18" rx="3" class="bar"></rect>')
        lines.append(f'<text x="{250 + bar_w:.1f}" y="{y + 14}" class="tiny">{_fmt_number(value)} {escape(note)}</text>')
        y += 28
    lines.append("</svg>")
    return "\n".join(lines)


def _boxplot(title: str, subtitle: str, values: list[float | None]) -> str:
    clean = [value for value in values if value is not None]
    if not clean:
        return _empty_chart(title, subtitle)
    width, height = 920, 260
    lower, upper = min(clean), max(clean)
    q1, med, q3 = percentile(clean, 0.25), median(clean), percentile(clean, 0.75)
    scale = lambda value: 100 + ((value - lower) / max(upper - lower, 1e-12)) * 720
    y = 138
    lines = [_svg_header(width, height), _svg_title(title, subtitle, width)]
    lines.append(f'<line x1="{scale(lower):.1f}" x2="{scale(upper):.1f}" y1="{y}" y2="{y}" class="axis-line"></line>')
    lines.append(f'<rect x="{scale(q1):.1f}" y="{y - 28}" width="{max(scale(q3) - scale(q1), 2):.1f}" height="56" rx="4" class="box"></rect>')
    lines.append(f'<line x1="{scale(med):.1f}" x2="{scale(med):.1f}" y1="{y - 36}" y2="{y + 36}" class="median"></line>')
    for value, label in ((lower, "min"), (q1, "q1"), (med, "median"), (q3, "q3"), (upper, "max")):
        lines.append(f'<text x="{scale(value):.1f}" y="220" class="tiny">{label}: {_fmt_usd(value)}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def _histogram_values(title: str, subtitle: str, values: list[float | None], *, x_label: str) -> str:
    clean = [value for value in values if value is not None]
    if not clean:
        return _empty_chart(title, subtitle)
    dist = distribution(clean)
    bins = _histogram(clean)
    return _bar_chart(title, f"{subtitle} p95={_fmt_number(dist['p95'])}. x={x_label}", [(f"{_fmt_number(row['lower'])}-{_fmt_number(row['upper'])}", row["count"]) for row in bins], y_label="Requests")


def _line_chart(title: str, subtitle: str, points: list[tuple[Any, Any]], *, x_label: str, y_label: str) -> str:
    clean = [(_num(x), _num(y)) for x, y in points]
    clean = [(x, y) for x, y in clean if x is not None and y is not None]
    if not clean:
        return _empty_chart(title, subtitle)
    width, height = 920, 320
    xs, ys = [x for x, _ in clean], [y for _, y in clean]
    sx = lambda value: 90 + ((value - min(xs)) / max(max(xs) - min(xs), 1e-12)) * 760
    sy = lambda value: 250 - ((value - min(ys)) / max(max(ys) - min(ys), 1e-12)) * 170
    d = " ".join(("M" if index == 0 else "L") + f"{sx(x):.1f},{sy(y):.1f}" for index, (x, y) in enumerate(clean))
    lines = [_svg_header(width, height), _svg_title(title, subtitle, width), _axes(x_label, y_label), f'<path d="{d}" class="line"></path>', "</svg>"]
    return "\n".join(lines)


def _scatter_chart(title: str, subtitle: str, points: list[tuple[Any, Any, Any]], *, x_label: str, y_label: str) -> str:
    clean = [(_num(x), _num(y), str(label)) for x, y, label in points]
    clean = [(x, y, label) for x, y, label in clean if x is not None and y is not None]
    if not clean:
        return _empty_chart(title, subtitle)
    if len(clean) > 180:
        subtitle = f"{subtitle} Showing first 180 of {len(clean)} plotted points; full rows remain in tables/artifacts."
    width, height = 920, 320
    xs, ys = [x for x, _y, _label in clean], [y for _x, y, _label in clean]
    sx = lambda value: 90 + ((value - min(xs)) / max(max(xs) - min(xs), 1e-12)) * 760
    sy = lambda value: 250 - ((value - min(ys)) / max(max(ys) - min(ys), 1e-12)) * 170
    lines = [_svg_header(width, height), _svg_title(title, subtitle, width), _axes(x_label, y_label)]
    for x, y, label in clean[:180]:
        lines.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" class="dot"><title>{escape(label)}</title></circle>')
    lines.append("</svg>")
    return "\n".join(lines)


def _empty_chart(title: str, subtitle: str) -> str:
    return "\n".join([_svg_header(920, 240), _svg_title(title, subtitle, 920), '<text x="24" y="140" class="empty">N/A / telemetry unavailable for this run</text>', "</svg>"])


def _svg_header(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">'
        "<style>text{font-family:ui-sans-serif,system-ui,sans-serif;fill:#1f2937}.title{font-size:18px;font-weight:700}.subtitle,.axis,.tiny{font-size:12px;fill:#6b7280}.label{font-size:13px}.bar{fill:#0f766e}.box{fill:#d1fae5;stroke:#0f766e}.median,.axis-line{stroke:#111827;stroke-width:2}.line{fill:none;stroke:#0f766e;stroke-width:2.5}.dot{fill:#0f766e;opacity:.78}.empty{font-size:14px;fill:#6b7280}</style>"
    )


def _svg_title(title: str, subtitle: str, width: int) -> str:
    del width
    return f'<text x="24" y="32" class="title">{escape(title)}</text><text x="24" y="54" class="subtitle">{escape(subtitle)}</text>'


def _axes(x_label: str, y_label: str) -> str:
    return f'<line x1="90" y1="250" x2="850" y2="250" class="axis-line"></line><line x1="90" y1="80" x2="90" y2="250" class="axis-line"></line><text x="390" y="295" class="axis">{escape(x_label)}</text><text x="24" y="74" class="axis">{escape(y_label)}</text>'


def _section(section_id: str, title: str, content: str) -> str:
    return f'<section id="{escape(section_id)}"><h2>{escape(title)}</h2>{content}</section>'


def _nav() -> str:
    items = [("Overview", "#overview"), ("Assignment Metrics", "#assignment-metrics"), ("Cost", "#cost"), ("Performance", "#performance"), ("Agents & Tools", "#agents-tools"), ("Inference & Serving", "#inference-serving"), ("Workload", "#workload"), ("Request Drilldown", "#request-drilldown"), ("Provenance", "#provenance"), ("Artifact Index", "#artifact-index")]
    return "<nav>" + "".join(f'<a href="{href}">{escape(label)}</a>' for label, href in items) + "</nav>"


def _definition_grid(rows: list[tuple[str, Any]]) -> str:
    return "<dl>" + "".join(f"<div><dt>{escape(label)}</dt><dd>{escape(_display(value))}</dd></div>" for label, value in rows) + "</dl>"


def _cost_profile_details(report: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    profile = provenance.get("cost_profile") or {}
    profile_id = profile.get("profile_id") or report.get("cost_profile")
    notes = str(profile.get("notes") or "")
    name = str(profile.get("name") or (str(profile_id).split(":")[0] if profile_id else ""))
    profile_type = "real/provider-based"
    if "synthetic" in notes.lower() or "reference" in name.lower() or "demo" in name.lower():
        profile_type = "synthetic/reference"
    return {"profile_id": profile_id, "name": name, "notes": notes, "machine_hourly_usd": profile.get("machine_hourly_usd"), "profile_type": profile_type}


def _is_illustrative_cost(profile: dict[str, Any]) -> bool:
    text = " ".join(str(profile.get(key) or "") for key in ("profile_id", "name", "notes", "profile_type")).lower()
    return "reference_cpu_demo" in text or "synthetic" in text or "illustrative" in text


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return read_parquet_rows(path) if path.exists() else []


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize(data), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _histogram(values: list[float], bins: int = 10) -> list[dict[str, float | int]]:
    lower, upper = min(values), max(values)
    if lower == upper:
        return [{"lower": lower, "upper": upper, "count": len(values)}]
    width = (upper - lower) / bins
    counts = [0] * bins
    for value in values:
        counts[min(int((value - lower) / width), bins - 1)] += 1
    return [{"lower": lower + index * width, "upper": lower + (index + 1) * width, "count": count} for index, count in enumerate(counts)]


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _agent_tool_label(row: dict[str, Any]) -> str:
    return f"{row.get('agent')} / {row.get('tool')}" if row.get("tool") else str(row.get("agent"))


def _sum_optional(*values: Any) -> float | None:
    clean = [_num(value) for value in values]
    clean = [value for value in clean if value is not None]
    return sum(clean) if clean else None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _display(value: Any) -> str:
    if value is None or value == "":
        return "N/A / telemetry unavailable for this run"
    if isinstance(value, float):
        return _fmt_number(value)
    return str(value)


def _fmt_value(value: Any, unit: str | None) -> str:
    if value is None:
        return "N/A / telemetry unavailable for this run"
    if unit == "percent":
        return _fmt_percent(value)
    if unit == "bytes":
        return _fmt_bytes(value)
    if unit == "tokens":
        return f"{_fmt_number(value)} tokens"
    return f"{_fmt_number(value)} {unit or ''}".strip()


def _fmt_number(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A / telemetry unavailable for this run"
    return f"{number:,.2f}" if abs(number) >= 1000 else f"{number:.6g}"


def _fmt_usd(value: Any) -> str:
    number = _num(value)
    return "N/A / telemetry unavailable for this run" if number is None else f"${number:,.6f}"


def _fmt_ms(value: Any) -> str:
    number = _num(value)
    return "N/A / telemetry unavailable for this run" if number is None else f"{number:,.2f} ms"


def _fmt_seconds(value: Any) -> str:
    number = _num(value)
    return "N/A / telemetry unavailable for this run" if number is None else f"{number:,.2f} s"


def _fmt_percent(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A / telemetry unavailable for this run"
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return f"{number:.2f}%"


def _fmt_bytes(value: Any) -> str:
    number = _num(value)
    return "N/A / telemetry unavailable for this run" if number is None else f"{number / (1024 ** 3):.2f} GiB"


def _success_rate(report: dict[str, Any]) -> str:
    total = _num(report.get("request_count"))
    success = _num(report.get("success_count"))
    if not total or success is None:
        return "N/A / telemetry unavailable for this run"
    return _fmt_percent(success / total)


def _yes_no(value: Any) -> str:
    if value is None:
        return "N/A / telemetry unavailable for this run"
    return "enabled" if bool(value) else "disabled"


def _safe_filename(value: Any) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in str(value))[:80]


def _slug(value: str) -> str:
    return value.lower().replace(" & ", "-").replace(" ", "-")


def _css() -> str:
    return """
:root{color:#1c1917;background:#f5f5f4;--line:#d6d3d1;--surface:#fff;--muted:#57534e;--ink:#1c1917;--accent:#0f766e;--warn:#9a3412}
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;color:var(--ink);background:#f5f5f4}
header{padding:28px 32px 16px;border-bottom:1px solid var(--line);background:#fff}
h1{margin:0 0 6px;font-size:24px;line-height:1.2}header p{margin:0;color:var(--muted)}
nav{position:sticky;top:0;z-index:2;display:flex;gap:16px;overflow:auto;padding:10px 32px;border-bottom:1px solid var(--line);background:#fff}
nav a{color:#44403c;text-decoration:none;font-size:14px;white-space:nowrap}nav a:hover{color:var(--accent)}
main{max-width:1180px;margin:0 auto;padding:24px 24px 56px}
section{margin:0 0 28px;padding:22px 24px;background:var(--surface);border:1px solid var(--line);border-radius:8px}
h2{margin:0 0 18px;font-size:20px}h3{margin:24px 0 10px;font-size:15px}
dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:6px;overflow:hidden}
dl div{background:#fff;padding:12px}dt{font-size:12px;color:var(--muted);margin-bottom:4px}dd{margin:0;font-size:14px;font-weight:650;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:9px 8px;text-align:left;vertical-align:top}th{font-weight:700;background:#fafaf9}
.chart-grid{display:grid;grid-template-columns:1fr;gap:18px}figure{margin:0}img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#fff}figcaption,.note,.empty{color:var(--muted);font-size:13px}.notice{padding:10px 12px;margin:0 0 16px;border:1px solid #fdba74;background:#fff7ed;color:var(--warn);border-radius:6px;font-weight:700}
@media (min-width:900px){.chart-grid{grid-template-columns:1fr 1fr}}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a static visual analytics report")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    print(generate_visual_report(args.run_dir))


if __name__ == "__main__":
    main()
