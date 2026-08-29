"""Command-line entrypoint for the Phase 5 Gateway benchmark."""

from __future__ import annotations

import argparse
import asyncio

from .config import BenchmarkConfig
from .runner import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Gateway portfolio benchmark")
    parser.add_argument("--dataset-mode", default="canonical_100")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--gateway-base-url", default="http://localhost:8000")
    parser.add_argument("--request-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--sample-seed", type=int, default=1)
    parser.add_argument("--run-id")
    parser.add_argument("--run-name")
    parser.add_argument("--output-root", default="results")
    args = parser.parse_args()

    result = asyncio.run(
        run_benchmark(
            BenchmarkConfig(
                dataset_mode=args.dataset_mode,
                concurrency=args.concurrency,
                gateway_base_url=args.gateway_base_url,
                request_timeout_seconds=args.request_timeout_seconds,
                sample_size=args.sample_size,
                sample_seed=args.sample_seed,
                run_id=args.run_id,
                run_name=args.run_name,
                output_root=args.output_root,
            )
        )
    )
    print(result.output_dir or result.run_id)


if __name__ == "__main__":
    main()
