from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.portfolio.deployment.collector import build_dataset_from_artifacts
from portfolio.portfolio.deployment.context_lengths import (
    choose_max_model_len,
    measure_context_lengths,
)
from portfolio.portfolio.deployment.provenance import redact_secrets
from portfolio.portfolio.deployment.telemetry import (
    DEFAULT_PROMETHEUS_QUERIES,
    DEFAULT_VLLM_QUERIES,
)


ROOT = Path(__file__).resolve().parents[1]


class Phase8DeploymentTests(unittest.TestCase):
    def test_compose_files_are_pinned_and_keep_inference_private(self):
        common = (ROOT / "deploy" / "compose" / "compose.common.yaml").read_text()
        cpu = (ROOT / "deploy" / "compose" / "compose.cpu.yaml").read_text()
        gpu = (ROOT / "deploy" / "compose" / "compose.gpu.yaml").read_text()
        stress = (ROOT / "deploy" / "compose" / "compose.gateway-stress.yaml").read_text()
        combined = "\n".join([common, cpu, gpu, stress])

        self.assertNotIn(":latest", combined)
        self.assertNotIn("redis", combined.lower())
        self.assertIn("POSTGRES_PASSWORD", common)
        self.assertIn("http://vllm:8000/v1", common)
        self.assertNotRegex(cpu, r"ports:\s*\n\s*-\s*[\"']?\d+:8000")
        self.assertNotRegex(gpu, r"ports:\s*\n\s*-\s*[\"']?\d+:8000")
        self.assertIn("condition: service_healthy", common)
        self.assertIn("pg_isready", common)
        self.assertIn("/v1/models", cpu)
        self.assertIn("/v1/models", gpu)
        self.assertIn("fake_portfolio", stress)

    def test_prometheus_cpu_and_gpu_target_split(self):
        cpu_config = (ROOT / "observability" / "prometheus" / "prometheus.yml").read_text()
        gpu_config = (
            ROOT / "observability" / "prometheus" / "prometheus.gpu.yml"
        ).read_text()

        self.assertIn("vllm:8000", cpu_config)
        self.assertIn("cadvisor:8080", cpu_config)
        self.assertNotIn("dcgm-exporter:9400", cpu_config)
        self.assertIn("dcgm-exporter:9400", gpu_config)

    def test_inference_profiles_capture_canonical_model_and_vllm_version(self):
        for name in (
            "local-cpu.yaml",
            "cloud-gpu-baseline.yaml",
            "cloud-gpu-prefix-cache.yaml",
        ):
            text = (ROOT / "configs" / "inference" / name).read_text()
            self.assertIn("Qwen/Qwen3-4B-Instruct-2507", text)
            self.assertIn("advisor_base_url: http://vllm:8000/v1", text)
            self.assertIn("vllm_version: 0.8.5", text)
            self.assertIn("max_model_len", text)

    def test_context_length_measurement_uses_tokenizer_and_headroom(self):
        class FakeTokenizer:
            def encode(self, prompt, add_special_tokens=True):
                return list(range(max(1, len(prompt) // 8)))

        with patch(
            "portfolio.portfolio.deployment.context_lengths._load_tokenizer",
            return_value={"status": "ok", "tokenizer": FakeTokenizer()},
        ):
            result = measure_context_lengths(
                dataset_mode="sampled_2",
                sample_seed=9,
                generation_allowance=128,
            )

        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["prompt_count"], 2)
        self.assertGreaterEqual(
            result["recommended_max_model_len"],
            result["prompt_tokens"]["max"] + result["generation_allowance_tokens"],
        )
        self.assertEqual(choose_max_model_len(max_prompt_tokens=100, generation_allowance=128), 1024)

    def test_context_length_reports_tokenizer_unavailable_without_fake_measurement(self):
        with patch(
            "portfolio.portfolio.deployment.context_lengths._load_tokenizer",
            return_value={"status": "error", "error": "missing transformers"},
        ):
            result = measure_context_lengths(dataset_mode="canonical_100")

        self.assertEqual(result["status"], "tokenizer_unavailable")
        self.assertIn("missing transformers", result["error"])
        self.assertNotIn("recommended_max_model_len", result)

    def test_collector_maps_jaeger_spans_to_stable_inference_observation_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "raw" / "run-1"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "started_at": "2026-08-27T00:00:00+00:00",
                        "finished_at": "2026-08-27T00:01:00+00:00",
                        "dataset_mode": "sampled_1",
                    }
                )
                + "\n"
            )
            (run_dir / "requests.jsonl").write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "request_id": "request-1",
                        "query_id": "query-1",
                        "n_holdings": 2,
                        "phrasing": "risk",
                        "lookback_days": 90,
                        "start_timestamp": "2026-08-27T00:00:01+00:00",
                        "finish_timestamp": "2026-08-27T00:00:02+00:00",
                        "client_latency_ms": 1000,
                        "http_status": 200,
                        "success": True,
                    }
                )
                + "\n"
            )
            trace_path = root / "trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "data": [
                            {
                                "traceID": "trace-a",
                                "spans": [
                                    {
                                        "traceID": "trace-a",
                                        "spanID": "span-b",
                                        "operationName": "AdvisorAgent.inference",
                                        "startTime": 1_000_000,
                                        "duration": 50_000,
                                        "tags": [
                                            {"key": "request_id", "value": "request-1"},
                                            {"key": "query_id", "value": "query-1"},
                                            {"key": "agent", "value": "AdvisorAgent"},
                                            {"key": "stage", "value": "advisor"},
                                            {"key": "model", "value": "Qwen/Qwen3-4B-Instruct-2507"},
                                            {"key": "prompt_tokens", "value": 111},
                                            {"key": "completion_tokens", "value": 22},
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                )
            )

            dataset = build_dataset_from_artifacts(run_dir, jaeger_trace_path=trace_path)

        self.assertEqual(len(dataset.inference_observations), 1)
        row = dataset.inference_observations[0]
        self.assertEqual(row.raw["observation_id"], "otel:trace-a:span-b")
        self.assertEqual(row.prompt_tokens, 111)
        self.assertEqual(row.completion_tokens, 22)

    def test_provenance_redacts_secrets_recursively(self):
        value = redact_secrets(
            {
                "POSTGRES_PASSWORD": "secret",
                "nested": {"HF_TOKEN": "token", "safe": "kept"},
            }
        )
        self.assertEqual(value["POSTGRES_PASSWORD"], "<redacted>")
        self.assertEqual(value["nested"]["HF_TOKEN"], "<redacted>")
        self.assertEqual(value["nested"]["safe"], "kept")

    def test_prometheus_export_queries_do_not_use_request_identity_labels(self):
        query_text = "\n".join(
            [*DEFAULT_PROMETHEUS_QUERIES.values(), *DEFAULT_VLLM_QUERIES.values()]
        )
        for forbidden in ("request_id", "query_id", "run_id", "ticker"):
            self.assertNotIn(forbidden, query_text)
        self.assertIn("vllm:num_requests_waiting", DEFAULT_VLLM_QUERIES)


if __name__ == "__main__":
    unittest.main()
