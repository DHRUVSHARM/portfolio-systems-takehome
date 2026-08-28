"""Deployment cost-profile generation and validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any

from ..analytics.profiles import load_cost_profile


class CostProfileConfigurationError(ValueError):
    """Raised when a canonical cost profile would be ambiguous or invalid."""


def create_cloud_gpu_cost_profile_from_env(
    *,
    output_dir: Path | str,
    base_profile_path: Path | str = "configs/cost/reference_local.yaml",
    provider_name: str | None = None,
    instance_type: str | None = None,
    machine_hourly_usd: str | float | None = None,
) -> Path:
    provider = (provider_name or os.getenv("PROVIDER_NAME") or "").strip()
    instance = (instance_type or os.getenv("PROVIDER_INSTANCE_TYPE") or "").strip()
    hourly_raw = machine_hourly_usd if machine_hourly_usd is not None else os.getenv("MACHINE_HOURLY_USD")
    hourly = _parse_hourly(hourly_raw)
    _validate_canonical_provider(provider=provider, instance_type=instance, hourly=hourly)

    base = load_cost_profile(base_profile_path)
    name = f"cloud_gpu_{_slug(provider)}_{_slug(instance)}"
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(output_dir) / f"{name}_{version}.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(
            [
                f"name: {name}",
                f'version: "{version}"',
                "currency: USD",
                f"machine_hourly_usd: {hourly:.6f}",
                f"cpu_pool_fraction: {base.cpu_pool_fraction}",
                f"gpu_pool_fraction: {base.gpu_pool_fraction}",
                f"overhead_pool_fraction: {base.overhead_pool_fraction}",
                f"cpu_attribution_method: {base.cpu_attribution_method}",
                f"gpu_attribution_method: {base.gpu_attribution_method}",
                f"prefill_token_weight: {base.prefill_token_weight}",
                f"decode_token_weight: {base.decode_token_weight}",
                "notes: >",
                "  Canonical cloud GPU experiment profile generated from provider",
                f"  metadata. Provider={provider}; instance_type={instance};",
                "  hourly rate is the bundled VM price and must be verified",
                "  against the provider bill/rate card before final reporting.",
                "",
            ]
        )
    )
    return output_path


def validate_canonical_cost_profile(path: Path | str) -> None:
    profile = load_cost_profile(path)
    notes = (profile.notes or "").lower()
    name = profile.name.lower()
    if profile.machine_hourly_usd <= 0:
        raise CostProfileConfigurationError("canonical GPU runs require MACHINE_HOURLY_USD > 0")
    if "local" in name or "demo" in name or "synthetic" in name or "synthetic" in notes:
        raise CostProfileConfigurationError(
            "canonical GPU runs cannot use local/demo/synthetic cost profiles"
        )


def _validate_canonical_provider(*, provider: str, instance_type: str, hourly: float) -> None:
    if not provider:
        raise CostProfileConfigurationError("PROVIDER_NAME is required")
    if provider.lower() == "local":
        raise CostProfileConfigurationError("PROVIDER_NAME must not be local for canonical GPU runs")
    if not instance_type:
        raise CostProfileConfigurationError("PROVIDER_INSTANCE_TYPE is required")
    if instance_type.lower() == "local":
        raise CostProfileConfigurationError(
            "PROVIDER_INSTANCE_TYPE must not be local for canonical GPU runs"
        )
    if hourly <= 0:
        raise CostProfileConfigurationError("MACHINE_HOURLY_USD must be greater than zero")


def _parse_hourly(value: str | float | None) -> float:
    if value is None or value == "":
        raise CostProfileConfigurationError("MACHINE_HOURLY_USD is required")
    try:
        return float(value)
    except ValueError as exc:
        raise CostProfileConfigurationError("MACHINE_HOURLY_USD must be numeric") from exc


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/validate deployment cost profiles")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-cloud-gpu")
    create.add_argument("--output-dir", required=True, type=Path)
    create.add_argument("--base-profile", default="configs/cost/reference_local.yaml", type=Path)
    create.add_argument("--provider-name")
    create.add_argument("--instance-type")
    create.add_argument("--machine-hourly-usd")

    validate = subparsers.add_parser("validate-canonical")
    validate.add_argument("--profile", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "create-cloud-gpu":
        print(
            create_cloud_gpu_cost_profile_from_env(
                output_dir=args.output_dir,
                base_profile_path=args.base_profile,
                provider_name=args.provider_name,
                instance_type=args.instance_type,
                machine_hourly_usd=args.machine_hourly_usd,
            )
        )
    elif args.command == "validate-canonical":
        validate_canonical_cost_profile(args.profile)
        print(args.profile)


if __name__ == "__main__":
    main()
