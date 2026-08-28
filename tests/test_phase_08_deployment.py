from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio.portfolio.analytics.profiles import load_cost_profile
from portfolio.portfolio.deployment.collector import (
    TelemetryRequiredError,
    build_dataset_from_artifacts,
    collect_historical_artifacts,
)
from portfolio.portfolio.deployment.context_lengths import (
    choose_max_model_len,
    measure_context_lengths,
)
from portfolio.portfolio.deployment.cost_profiles import (
    CostProfileConfigurationError,
    create_cloud_gpu_cost_profile_from_env,
    validate_canonical_cost_profile,
)
from portfolio.portfolio.deployment.provenance import (
    build_run_provenance,
    parse_driver_cuda_version,
    parse_nvidia_smi_gpu_query,
    redact_secrets,
)
from portfolio.portfolio.deployment.telemetry import (
    DEFAULT_PROMETHEUS_QUERIES,
    DEFAULT_VLLM_QUERIES,
    inspect_vllm_metrics_text,
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
        self.assertIn("deployment-tools", common)

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

    def test_collector_maps_only_inference_request_to_inference_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "raw" / "run-1"
            _write_benchmark_artifacts(run_dir)
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
                                        "spanID": "span-a",
                                        "operationName": "AdvisorAgent.summarize",
                                        "startTime": 1_000_000,
                                        "duration": 80_000,
                                        "tags": [
                                            {"key": "request_id", "value": "request-1"},
                                            {"key": "query_id", "value": "query-1"},
                                            {"key": "agent", "value": "AdvisorAgent"},
                                            {"key": "stage", "value": "advisor"},
                                        ],
                                    },
                                    {
                                        "traceID": "trace-a",
                                        "spanID": "span-b",
                                        "operationName": "inference.request",
                                        "startTime": 1_010_000,
                                        "duration": 50_000,
                                        "references": [
                                            {
                                                "refType": "CHILD_OF",
                                                "traceID": "trace-a",
                                                "spanID": "span-a",
                                            }
                                        ],
                                        "tags": [
                                            {"key": "request_id", "value": "request-1"},
                                            {"key": "query_id", "value": "query-1"},
                                            {"key": "stage", "value": "inference"},
                                            {"key": "model", "value": "Qwen/Qwen3-4B-Instruct-2507"},
                                            {"key": "http.status_code", "value": 200},
                                            {"key": "llm.prompt_tokens", "value": 111},
                                            {"key": "llm.completion_tokens", "value": 22},
                                            {"key": "llm.total_tokens", "value": 133},
                                            {"key": "inference.attempt", "value": 1},
                                            {"key": "inference.retry_count", "value": 0},
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                )
            )

            dataset = build_dataset_from_artifacts(run_dir, jaeger_trace_path=trace_path)

        self.assertEqual(len(dataset.execution_observations), 1)
        self.assertEqual(dataset.execution_observations[0].agent, "AdvisorAgent")
        self.assertEqual(len(dataset.inference_observations), 1)
        row = dataset.inference_observations[0]
        self.assertEqual(row.raw["observation_id"], "otel:trace-a:span-b")
        self.assertEqual(row.prompt_tokens, 111)
        self.assertEqual(row.completion_tokens, 22)
        self.assertEqual(row.total_tokens, 133)
        self.assertEqual(row.model, "Qwen/Qwen3-4B-Instruct-2507")
        self.assertEqual(row.status, 200)

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
        self.assertIn("histogram_quantile", DEFAULT_VLLM_QUERIES["vllm:e2e_p95"])

    def test_missing_vllm_metrics_are_unavailable_not_zero(self):
        report = inspect_vllm_metrics_text(
            """
            # HELP vllm:num_requests_running Running requests.
            vllm:num_requests_running 1
            """
        )
        self.assertEqual(report["vllm:num_requests_running"]["status"], "available")
        self.assertEqual(report["vllm:num_requests_waiting"]["status"], "unavailable")
        self.assertIsNone(report["vllm:num_requests_waiting"]["matched_name"])

    def test_required_telemetry_failure_marks_incomplete_and_preserves_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "raw" / "run-1"
            _write_benchmark_artifacts(run_dir)
            output_dir = root / "analytics"

            with self.assertRaises(TelemetryRequiredError):
                collect_historical_artifacts(
                    run_dir,
                    cost_profile_path=ROOT / "configs" / "cost" / "reference_local.yaml",
                    output_dir=output_dir,
                    require_telemetry=True,
                )

            marker = json.loads((output_dir / "incomplete_report.json").read_text())

        self.assertEqual(marker["status"], "incomplete")
        self.assertTrue(marker["raw_benchmark_artifacts_preserved"])

    def test_repeated_collector_import_is_idempotent_for_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "raw" / "run-1"
            _write_benchmark_artifacts(run_dir)
            trace_path = root / "trace.json"
            prom_path = root / "prometheus.json"
            trace_path.write_text(_trace_json())
            prom_path.write_text(_prometheus_json())
            output_dir = root / "analytics"

            first = collect_historical_artifacts(
                run_dir,
                cost_profile_path=ROOT / "configs" / "cost" / "reference_cpu_demo.yaml",
                output_dir=output_dir,
                jaeger_trace_path=trace_path,
                prometheus_samples_path=prom_path,
                require_telemetry=True,
            )
            second = collect_historical_artifacts(
                run_dir,
                cost_profile_path=ROOT / "configs" / "cost" / "reference_cpu_demo.yaml",
                output_dir=output_dir,
                jaeger_trace_path=trace_path,
                prometheus_samples_path=prom_path,
                require_telemetry=True,
            )
            report_exists = (second / "report.json").exists()

        self.assertEqual(first, second)
        self.assertTrue(report_exists)

    def test_cpu_synthetic_demo_cost_profile_is_noncanonical_and_nonzero(self):
        profile = load_cost_profile(ROOT / "configs" / "cost" / "reference_cpu_demo.yaml")
        self.assertEqual(profile.name, "reference_cpu_demo")
        self.assertGreater(profile.machine_hourly_usd, 0.0)
        self.assertIn("NONCANONICAL", profile.notes or "")
        self.assertIn("SYNTHETIC", profile.notes or "")

    def test_canonical_gpu_path_refuses_zero_or_demo_cost_profiles(self):
        with self.assertRaises(CostProfileConfigurationError):
            validate_canonical_cost_profile(ROOT / "configs" / "cost" / "reference_local.yaml")
        with self.assertRaises(CostProfileConfigurationError):
            validate_canonical_cost_profile(ROOT / "configs" / "cost" / "reference_cpu_demo.yaml")

    def test_real_gpu_cost_profile_and_provider_metadata_enter_provenance(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ",
            {
                "PROVIDER_NAME": "ExampleCloud",
                "PROVIDER_INSTANCE_TYPE": "gpu-a10",
                "MACHINE_HOURLY_USD": "2.50",
            },
            clear=False,
        ):
            profile_path = create_cloud_gpu_cost_profile_from_env(output_dir=temp)
            validate_canonical_cost_profile(profile_path)
            provenance = build_run_provenance(
                run_id="run-1",
                cost_profile_path=profile_path,
            )

        self.assertEqual(provenance["environment"]["PROVIDER_NAME"], "ExampleCloud")
        self.assertEqual(provenance["environment"]["MACHINE_HOURLY_USD"], "2.50")
        self.assertEqual(provenance["cost_profile"]["machine_hourly_usd"], 2.5)
        self.assertIn("cloud_gpu_examplecloud_gpu_a10", provenance["cost_profile"]["name"])

    def test_gpu_script_automatically_exports_required_telemetry_and_profiles(self):
        script = (ROOT / "deploy" / "experiments" / "phase8_gpu_experiments.sh").read_text()
        self.assertIn("export_required_telemetry \"$run_id\"", script)
        self.assertIn("--include-gpu", script)
        self.assertIn("--include-vllm", script)
        self.assertIn("--jaeger-trace-json", script)
        self.assertIn("--prometheus-samples-json", script)
        self.assertIn("--require-telemetry", script)
        self.assertIn("telemetry_incomplete.json", script)
        self.assertIn("cloud-gpu-baseline.yaml", script)
        self.assertIn("cloud-gpu-prefix-cache.yaml", script)
        self.assertIn("PREFIX_CACHE_CONCURRENCY:?", script)
        self.assertIn("FULL_1000_CONCURRENCY:?", script)

    def test_vllm_recreation_waits_and_warms_before_prefix_measurement(self):
        script = (ROOT / "deploy" / "experiments" / "phase8_gpu_experiments.sh").read_text()
        off_index = script.index("VLLM_ENABLE_PREFIX_CACHING= compose up -d --force-recreate vllm")
        off_wait = script.index("wait_stack_ready", off_index)
        off_warm = script.index("VLLM_ENABLE_PREFIX_CACHING= warmup", off_wait)
        off_run = script.index("VLLM_ENABLE_PREFIX_CACHING= run_benchmark", off_warm)
        on_index = script.index("VLLM_ENABLE_PREFIX_CACHING=1 compose up -d --force-recreate vllm")
        on_wait = script.index("wait_stack_ready", on_index)
        on_warm = script.index("VLLM_ENABLE_PREFIX_CACHING=1 warmup", on_wait)
        on_run = script.index("VLLM_ENABLE_PREFIX_CACHING=1 run_benchmark", on_warm)
        self.assertLess(off_index, off_wait)
        self.assertLess(off_wait, off_warm)
        self.assertLess(off_warm, off_run)
        self.assertLess(on_index, on_wait)
        self.assertLess(on_wait, on_warm)
        self.assertLess(on_warm, on_run)

    def test_context_tooling_has_reproducible_runtime_path(self):
        requirements = (ROOT / "deploy" / "tools-requirements.txt").read_text()
        common = (ROOT / "deploy" / "compose" / "compose.common.yaml").read_text()
        self.assertIn("transformers==4.56.2", requirements)
        self.assertIn("deployment-tools", common)
        self.assertIn("deployment-tools.Dockerfile", common)

    def test_gpu_provenance_survives_missing_optional_cuda_metadata(self):
        parsed = parse_nvidia_smi_gpu_query("NVIDIA A10, 23028, 535.183.01")
        self.assertEqual(parsed["name"], "NVIDIA A10")
        self.assertEqual(parsed["memory_total_mb"], "23028")
        self.assertEqual(parsed["driver_version"], "535.183.01")
        self.assertIsNone(parsed["driver_supported_cuda_version"])
        self.assertEqual(
            parse_driver_cuda_version("| NVIDIA-SMI 535.183.01    CUDA Version: 12.2 |"),
            "12.2",
        )


def _write_benchmark_artifacts(run_dir: Path) -> None:
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


def _trace_json() -> str:
    return json.dumps(
        {
            "data": [
                {
                    "traceID": "trace-a",
                    "spans": [
                        {
                            "traceID": "trace-a",
                            "spanID": "span-b",
                            "operationName": "inference.request",
                            "startTime": 1_000_000,
                            "duration": 50_000,
                            "tags": [
                                {"key": "request_id", "value": "request-1"},
                                {"key": "query_id", "value": "query-1"},
                                {"key": "stage", "value": "inference"},
                                {"key": "model", "value": "Qwen/Qwen3-4B-Instruct-2507"},
                                {"key": "http.status_code", "value": 200},
                                {"key": "llm.prompt_tokens", "value": 10},
                                {"key": "llm.completion_tokens", "value": 5},
                                {"key": "llm.total_tokens", "value": 15},
                            ],
                        }
                    ],
                }
            ]
        }
    )


def _prometheus_json() -> str:
    return json.dumps(
        {
            "data": {
                "result": [
                    {
                        "metric": {"name": "gateway_inflight_requests", "job": "gateway"},
                        "values": [[1_000_000, "1"]],
                    }
                ]
            }
        }
    )


if __name__ == "__main__":
    unittest.main()
