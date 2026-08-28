"""Resolve immutable Hugging Face model revisions for canonical runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import httpx


class ModelRevisionError(RuntimeError):
    """Raised when a model revision cannot be resolved or validated."""


def resolve_hf_model_revision(
    *,
    model: str,
    revision: str = "main",
    token: str | None = None,
) -> str:
    headers = {}
    auth_token = token or os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    url = f"https://huggingface.co/api/models/{model}/revision/{revision}"
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
    sha = str(response.json().get("sha") or "")
    if not is_commit_sha(sha):
        raise ModelRevisionError(f"resolved revision was not an immutable commit SHA: {sha!r}")
    return sha


def verify_revision_is_immutable(revision: str) -> None:
    if not is_commit_sha(revision):
        raise ModelRevisionError("canonical MODEL_REVISION must be a 40-character commit SHA")


def is_commit_sha(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"[0-9a-f]{40}", value))


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve immutable Hugging Face revisions")
    parser.add_argument("--model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    sha = resolve_hf_model_revision(model=args.model, revision=args.revision)
    payload = {"model": args.model, "source_revision": args.revision, "resolved_revision": sha}
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
