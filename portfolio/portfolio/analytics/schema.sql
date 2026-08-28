CREATE TABLE IF NOT EXISTS analytics_schema_versions (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO analytics_schema_versions (version)
VALUES (1)
ON CONFLICT (version) DO NOTHING;

INSERT INTO analytics_schema_versions (version)
VALUES (2)
ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS experiment_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  duration_seconds DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL,
  invalid_reason TEXT,
  run_name TEXT,
  dataset_mode TEXT,
  selected_query_count INTEGER,
  selection_manifest JSONB,
  sample_seed INTEGER,
  benchmark_concurrency INTEGER,
  gateway_max_in_flight INTEGER,
  gateway_queue_capacity INTEGER,
  workflow_cpu_workers INTEGER,
  max_concurrent_metric_tasks INTEGER,
  model TEXT,
  model_revision TEXT,
  vllm_version TEXT,
  dtype TEXT,
  max_model_len INTEGER,
  max_num_seqs INTEGER,
  max_num_batched_tokens INTEGER,
  gpu_memory_utilization DOUBLE PRECISION,
  prefix_caching_enabled BOOLEAN,
  hardware_profile JSONB,
  git_commit TEXT,
  config_hashes JSONB,
  cost_profile_name TEXT,
  cost_profile_version TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  query_id TEXT NOT NULL,
  n_holdings INTEGER NOT NULL,
  phrasing TEXT,
  lookback_days INTEGER NOT NULL,
  start_timestamp TIMESTAMPTZ NOT NULL,
  finish_timestamp TIMESTAMPTZ NOT NULL,
  client_latency_ms DOUBLE PRECISION NOT NULL,
  gateway_latency_ms DOUBLE PRECISION,
  gateway_queue_wait_ms DOUBLE PRECISION,
  http_status INTEGER,
  success BOOLEAN NOT NULL,
  error_type TEXT,
  response_body JSONB,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_requests_run_query ON requests(run_id, query_id);
CREATE INDEX IF NOT EXISTS idx_requests_success ON requests(run_id, success);

CREATE TABLE IF NOT EXISTS execution_observations (
  observation_id TEXT PRIMARY KEY,
  parent_observation_id TEXT REFERENCES execution_observations(observation_id),
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  request_id TEXT NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
  query_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  agent TEXT NOT NULL,
  tool TEXT,
  ticker TEXT,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL,
  wall_time_ms DOUBLE PRECISION NOT NULL,
  cpu_time_ms DOUBLE PRECISION,
  status TEXT NOT NULL,
  error_type TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_execution_request_parent
  ON execution_observations(run_id, request_id, parent_observation_id);
CREATE INDEX IF NOT EXISTS idx_execution_agent_tool
  ON execution_observations(run_id, agent, tool);
CREATE INDEX IF NOT EXISTS idx_execution_ticker
  ON execution_observations(run_id, ticker);

CREATE TABLE IF NOT EXISTS inference_observations (
  inference_id BIGSERIAL PRIMARY KEY,
  observation_key TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  request_id TEXT NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
  query_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  model TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  elapsed_ms DOUBLE PRECISION NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  ttft_ms DOUBLE PRECISION,
  queue_ms DOUBLE PRECISION,
  prefill_ms DOUBLE PRECISION,
  decode_ms DOUBLE PRECISION,
  generation_ms DOUBLE PRECISION,
  mean_itl_ms DOUBLE PRECISION,
  tpot_ms DOUBLE PRECISION,
  tokens_per_second DOUBLE PRECISION,
  status INTEGER NOT NULL,
  error_type TEXT,
  attempt_count INTEGER NOT NULL,
  retry_count INTEGER NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(observation_key)
);

CREATE INDEX IF NOT EXISTS idx_inference_run_request
  ON inference_observations(run_id, request_id);
CREATE INDEX IF NOT EXISTS idx_inference_model
  ON inference_observations(run_id, model);

CREATE TABLE IF NOT EXISTS resource_samples (
  sample_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  timestamp TIMESTAMPTZ NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  cpu_utilization DOUBLE PRECISION,
  memory_bytes BIGINT,
  gpu_utilization DOUBLE PRECISION,
  gpu_memory_used_bytes BIGINT,
  gpu_power_watts DOUBLE PRECISION,
  gpu_temperature_c DOUBLE PRECISION,
  gpu_energy_joules DOUBLE PRECISION,
  network_rx_bytes BIGINT,
  network_tx_bytes BIGINT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, timestamp, resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_resource_samples_run_time
  ON resource_samples(run_id, timestamp);

CREATE TABLE IF NOT EXISTS cost_profiles (
  profile_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  machine_hourly_usd DOUBLE PRECISION NOT NULL,
  cpu_pool_fraction DOUBLE PRECISION NOT NULL,
  gpu_pool_fraction DOUBLE PRECISION NOT NULL,
  overhead_pool_fraction DOUBLE PRECISION NOT NULL,
  cpu_attribution_method TEXT NOT NULL,
  gpu_attribution_method TEXT NOT NULL,
  prefill_token_weight DOUBLE PRECISION NOT NULL,
  decode_token_weight DOUBLE PRECISION NOT NULL,
  notes TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS metric_registry (
  metric_name TEXT NOT NULL,
  version TEXT NOT NULL,
  description TEXT NOT NULL,
  formula TEXT NOT NULL,
  inputs JSONB NOT NULL,
  PRIMARY KEY(metric_name, version)
);

CREATE TABLE IF NOT EXISTS cost_analyses (
  analysis_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES cost_profiles(profile_id),
  profile_name TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  total_run_cost_usd DOUBLE PRECISION NOT NULL,
  request_cost_sum_usd DOUBLE PRECISION NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, profile_id)
);

CREATE TABLE IF NOT EXISTS request_cost_attributions (
  attribution_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES cost_profiles(profile_id),
  request_id TEXT NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
  query_id TEXT NOT NULL,
  success BOOLEAN NOT NULL,
  total_cost_usd DOUBLE PRECISION NOT NULL,
  cpu_cost_usd DOUBLE PRECISION NOT NULL,
  gpu_cost_usd DOUBLE PRECISION NOT NULL,
  overhead_cost_usd DOUBLE PRECISION NOT NULL,
  cpu_seconds DOUBLE PRECISION NOT NULL,
  token_work DOUBLE PRECISION NOT NULL,
  wall_seconds DOUBLE PRECISION NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, profile_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_request_cost_run
  ON request_cost_attributions(run_id, profile_id, query_id);

CREATE TABLE IF NOT EXISTS agent_cost_attributions (
  attribution_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES cost_profiles(profile_id),
  agent TEXT NOT NULL,
  calls INTEGER NOT NULL,
  wall_time_ms DOUBLE PRECISION NOT NULL,
  cpu_time_ms DOUBLE PRECISION NOT NULL,
  p50_latency_ms DOUBLE PRECISION NOT NULL,
  p95_latency_ms DOUBLE PRECISION NOT NULL,
  p99_latency_ms DOUBLE PRECISION NOT NULL,
  failures INTEGER NOT NULL,
  attributed_cost_usd DOUBLE PRECISION NOT NULL,
  cost_percentage DOUBLE PRECISION NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, profile_id, agent)
);

CREATE TABLE IF NOT EXISTS derived_metrics (
  metric_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
  profile_id TEXT,
  metric_name TEXT NOT NULL,
  metric_version TEXT NOT NULL,
  calculated_at TIMESTAMPTZ NOT NULL,
  cost_profile_name TEXT,
  cost_profile_version TEXT,
  value JSONB NOT NULL,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, profile_id, metric_name, metric_version)
);

CREATE OR REPLACE VIEW agent_cost_breakdown AS
SELECT
  run_id,
  profile_id,
  agent,
  calls,
  cpu_time_ms / 1000.0 AS cpu_seconds,
  wall_time_ms,
  p95_latency_ms,
  attributed_cost_usd,
  cost_percentage,
  failures
FROM agent_cost_attributions;

CREATE OR REPLACE VIEW experiment_cost_comparison AS
SELECT
  r.run_id,
  r.dataset_mode,
  r.selected_query_count,
  r.benchmark_concurrency,
  r.model,
  r.max_num_seqs,
  c.profile_name,
  c.profile_version,
  c.profile_id,
  c.total_run_cost_usd,
  c.request_cost_sum_usd,
  r.duration_seconds
FROM experiment_runs r
JOIN cost_analyses c ON c.run_id = r.run_id;
