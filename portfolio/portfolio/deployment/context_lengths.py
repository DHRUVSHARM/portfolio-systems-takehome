"""Measure Advisor prompt context lengths for benchmark workloads."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from ..agents.advisor_prompt import build_advisor_prompt
from ..agents.metrics_agent import MetricsAgent
from ..agents.price_agent import PriceAgent
from ..agents.risk_agent import RiskAgent
from ..benchmark.config import BenchmarkConfig
from ..benchmark.datasets import (
    load_query_records,
    normalized_payload,
    select_query_records,
)
from ..analytics.models import distribution


DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
DEFAULT_GENERATION_ALLOWANCE = 256


def measure_context_lengths(
    *,
    dataset_mode: str = "canonical_100",
    sample_size: int | None = None,
    sample_seed: int = 1,
    model: str = DEFAULT_MODEL,
    revision: str | None = None,
    generation_allowance: int = DEFAULT_GENERATION_ALLOWANCE,
) -> dict[str, Any]:
    """Build real Advisor prompts and measure them with the requested tokenizer."""

    tokenizer_result = _load_tokenizer(model, revision=revision)
    if tokenizer_result["status"] != "ok":
        return {
            "status": "tokenizer_unavailable",
            "model": model,
            "model_revision": revision,
            "dataset_mode": dataset_mode,
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "generation_allowance_tokens": generation_allowance,
            "error": tokenizer_result["error"],
            "next_step": (
                "Run this command on a host that can install/import transformers "
                "and download the Qwen tokenizer before canonical GPU runs."
            ),
        }

    tokenizer = tokenizer_result["tokenizer"]
    prompts = _build_prompts(
        dataset_mode=dataset_mode,
        sample_size=sample_size,
        sample_seed=sample_seed,
    )
    token_counts = [
        len(tokenizer.encode(prompt, add_special_tokens=True)) for prompt in prompts
    ]
    prompt_dist = distribution([float(count) for count in token_counts])
    max_prompt_tokens = int(prompt_dist["max"])
    recommended = choose_max_model_len(
        max_prompt_tokens=max_prompt_tokens,
        generation_allowance=generation_allowance,
    )
    return {
        "status": "measured",
        "model": model,
        "model_revision": revision,
        "dataset_mode": dataset_mode,
        "sample_size": sample_size,
        "sample_seed": sample_seed,
        "prompt_count": len(prompts),
        "prompt_tokens": {
            **prompt_dist,
            "max": max_prompt_tokens,
            "p50": int(round(prompt_dist["p50"])),
            "p95": int(round(prompt_dist["p95"])),
            "p99": int(round(prompt_dist["p99"])),
        },
        "generation_allowance_tokens": generation_allowance,
        "recommended_max_model_len": recommended,
        "rationale": (
            "Ceil((max_prompt_tokens + generation_allowance) * 1.25) "
            "to the next 256-token boundary, with a 1024-token floor."
        ),
    }


def choose_max_model_len(
    *, max_prompt_tokens: int, generation_allowance: int, headroom_fraction: float = 0.25
) -> int:
    target = (max_prompt_tokens + generation_allowance) * (1.0 + headroom_fraction)
    return max(1024, int(math.ceil(target / 256.0) * 256))


def _build_prompts(
    *, dataset_mode: str, sample_size: int | None, sample_seed: int
) -> list[str]:
    config = BenchmarkConfig(
        dataset_mode=dataset_mode,
        concurrency=1,
        sample_size=sample_size,
        sample_seed=sample_seed,
        write_artifacts=False,
    )
    records, _selection = select_query_records(load_query_records(), config)
    metrics_agent = MetricsAgent(PriceAgent(use_yfinance=False))
    risk_agent = RiskAgent()
    prompts: list[str] = []
    for record in records:
        payload, lookback_days = normalized_payload(record)
        holdings = payload["holdings"]
        metrics = {
            ticker: metrics_agent.compute(ticker, lookback_days)
            for ticker in holdings
        }
        risk = risk_agent.assess(holdings, metrics)
        prompts.append(build_advisor_prompt(holdings=holdings, metrics=metrics, risk=risk))
    return prompts


def _load_tokenizer(model: str, revision: str | None = None) -> dict[str, Any]:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        return {"status": "error", "error": f"transformers unavailable: {exc}"}
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model,
            revision=revision,
            trust_remote_code=True,
        )
    except Exception as exc:  # network/auth/cache issues are expected locally sometimes
        return {"status": "error", "error": f"tokenizer load failed: {exc}"}
    return {"status": "ok", "tokenizer": tokenizer}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure Advisor prompt context lengths with a real tokenizer"
    )
    parser.add_argument("--dataset-mode", default="canonical_100")
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--sample-seed", type=int, default=1)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision")
    parser.add_argument(
        "--generation-allowance", type=int, default=DEFAULT_GENERATION_ALLOWANCE
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = measure_context_lengths(
        dataset_mode=args.dataset_mode,
        sample_size=args.sample_size,
        sample_seed=args.sample_seed,
        model=args.model,
        revision=args.revision,
        generation_allowance=args.generation_allowance,
    )
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
