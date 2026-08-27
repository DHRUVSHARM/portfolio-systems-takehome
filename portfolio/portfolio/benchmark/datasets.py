"""Dataset loading and deterministic query selection for benchmarks."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import random
from typing import Any

from ..benchmark_adapter import normalize_query_record, normalize_query_records
from .config import BenchmarkConfig


DEFAULT_LOOKBACK_DAYS = 365
REPO_ROOT = Path(__file__).resolve().parents[3]
QUERIES_PATH = REPO_ROOT / "queries.json"
CANONICAL_MANIFEST_PATH = REPO_ROOT / "benchmark_manifests" / "canonical_100.json"


def load_query_records(path: Path | str = QUERIES_PATH) -> list[dict[str, Any]]:
    with Path(path).open() as file:
        records = json.load(file)
    if not isinstance(records, list):
        raise ValueError("queries corpus must be a JSON list")
    return records


def load_canonical_manifest(
    path: Path | str = CANONICAL_MANIFEST_PATH,
) -> dict[str, Any]:
    with Path(path).open() as file:
        manifest = json.load(file)
    query_ids = manifest.get("query_ids")
    if not isinstance(query_ids, list):
        raise ValueError("canonical manifest must contain query_ids")
    if len(query_ids) != 100 or len(set(query_ids)) != 100:
        raise ValueError("canonical manifest must contain exactly 100 unique IDs")
    return manifest


def select_query_records(
    records: list[dict[str, Any]],
    config: BenchmarkConfig,
    *,
    manifest_path: Path | str = CANONICAL_MANIFEST_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select records for one run and validate adapter normalization."""

    by_id = {int(record["id"]): record for record in records}
    if len(by_id) != len(records):
        raise ValueError("query IDs must be unique")

    if config.dataset_mode == "canonical_100":
        manifest = load_canonical_manifest(manifest_path)
        ids = [int(query_id) for query_id in manifest["query_ids"]]
        selected = [_record_for_id(by_id, query_id) for query_id in ids]
        _validate_all_normalize(selected)
        return selected, {
            "selection_mode": "canonical_100",
            "manifest_path": str(Path(manifest_path)),
            "manifest_name": manifest.get("name", "canonical_100"),
            "selected_query_ids": ids,
            "selected_query_count": len(selected),
        }

    if config.dataset_mode == "full_1000":
        selected = list(records)
        _validate_all_normalize(selected)
        return selected, {
            "selection_mode": "full_1000",
            "selected_query_ids": [int(record["id"]) for record in selected],
            "selected_query_count": len(selected),
        }

    if config.dataset_mode.startswith("sampled_"):
        sample_size = config.resolved_sample_size()
        if sample_size is None:
            raise ValueError("sampled_N mode requires sample_size")
        if sample_size > len(records):
            raise ValueError("sample_size cannot exceed corpus size")
        rng = random.Random(config.sample_seed)
        selected = rng.sample(records, sample_size)
        _validate_all_normalize(selected)
        return selected, {
            "selection_mode": "sampled_N",
            "sample_size": sample_size,
            "sample_seed": config.sample_seed,
            "selected_query_ids": [int(record["id"]) for record in selected],
            "selected_query_count": len(selected),
        }

    raise ValueError(f"unsupported dataset_mode: {config.dataset_mode}")


def normalized_payload(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    normalized = normalize_query_record(record)
    lookback_days = normalized.lookback_days or DEFAULT_LOOKBACK_DAYS
    return {
        "holdings": normalized.holdings,
        "lookback_days": lookback_days,
    }, lookback_days


def query_strata_summary(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        "n_holdings": _string_keyed_counts(record["n_holdings"] for record in records),
        "phrasing": _string_keyed_counts(record["phrasing"] for record in records),
        "lookback_bucket": _string_keyed_counts(
            lookback_bucket(record["expected_lookback_days"]) for record in records
        ),
    }


def lookback_bucket(value: int | None) -> str:
    if value is None:
        return "none"
    if value <= 90:
        return "<=90"
    if value <= 180:
        return "<=180"
    if value <= 365:
        return "<=365"
    return ">365"


def _record_for_id(by_id: dict[int, dict[str, Any]], query_id: int) -> dict[str, Any]:
    try:
        return by_id[query_id]
    except KeyError as exc:
        raise ValueError(f"canonical manifest references missing query ID {query_id}") from exc


def _validate_all_normalize(records: list[dict[str, Any]]) -> None:
    normalize_query_records(records)


def _string_keyed_counts(values) -> dict[str, int]:
    return {str(key): count for key, count in sorted(Counter(values).items())}
