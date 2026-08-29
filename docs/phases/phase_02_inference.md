# Phase 2: Async vLLM Advisor Inference

## Goal

Replace the canonical serving Advisor path with an asynchronous OpenAI-compatible HTTP client suitable for vLLM while preserving the existing Advisor prompt semantics and the Phase 1 runtime guarantees.

Canonical path:

```text
PortfolioRuntime
  -> VLLMAdvisorAgent.summarize_async()
  -> reused httpx.AsyncClient
  -> POST /v1/chat/completions
  -> vLLM
  -> Qwen/Qwen3-4B-Instruct-2507
```

No real vLLM process or model download is required in this phase.

## Prerequisites

- Workload restoration complete.
- Phase 1 `PortfolioRuntime` complete.
- Read `docs/architecture/system_contracts.md` first.
- Inspect current `AdvisorAgent`, `PortfolioRuntime`, RequestContext and existing tests before editing.

## Existing code that must not be redesigned

- Metrics/Price/Risk formulas and responsibilities.
- Portfolio response shape.
- Phase 1 shared bounded CPU runtime.
- deterministic benchmark adapter.

## Components to add

Prefer a focused package such as:

```text
portfolio/portfolio/inference/
  __init__.py
  config.py
  models.py
  client.py

portfolio/portfolio/agents/
  vllm_advisor_agent.py
```

Exact file split may adapt to the current codebase, but avoid unnecessary frameworks or generic abstractions.

## Inference configuration

Create an immutable config with at least:

- `base_url`, default `http://vllm:8000/v1`
- `model`, default `Qwen/Qwen3-4B-Instruct-2507`
- `timeout_seconds`
- `max_tokens`, default `256`
- `temperature`, default `0.0`
- optional `api_key`
- `retry_count`, default `0`

Validate invalid/negative values.

Do not put vLLM engine knobs here. `max_num_seqs`, `max_model_len`, `gpu_memory_utilization`, prefix caching and batch-token configuration belong to deployment configuration in Phase 8.

## Async HTTP client

Use one long-lived `httpx.AsyncClient` reused across calls. Do not use `requests`, synchronous urllib, a thread pool for inference, or one AsyncClient per request.

Send:

```text
POST {base_url}/chat/completions
```

with a payload equivalent to:

```json
{
  "model": "Qwen/Qwen3-4B-Instruct-2507",
  "messages": [{"role": "user", "content": "<prompt>"}],
  "temperature": 0.0,
  "max_tokens": 256,
  "stream": false
}
```

Do not manually batch requests. vLLM owns continuous batching.

`retry_count=0` must result in exactly one HTTP attempt.

## Result and observation model

Do not return only the text internally. Create a typed result/observation containing at least:

- generated text
- returned/configured model
- prompt tokens
- completion tokens
- total tokens
- elapsed milliseconds or start/end timestamps
- run_id/request_id/query_id
- status
- attempt_count/retry_count

Use server `usage.prompt_tokens`, `usage.completion_tokens` and `usage.total_tokens`. Do not estimate token usage locally when the server provides it. Usage fields may be nullable only when genuinely absent.

Do not calculate cost in this layer.

## Correlation and observation sink

Allow `RequestContext` to flow into the Advisor/inference path. Support a lightweight optional callback/sink receiving each completed inference observation. The implementation must be request-safe; do not use mutable global `last_result` state. A no-op default sink is fine.

This phase does not implement PostgreSQL, Parquet or OpenTelemetry exporters.

## Advisor prompt

Preserve the existing Advisor prompt semantics exactly. Avoid two independent prompt implementations. Extract a shared prompt helper if necessary so legacy `AdvisorAgent` and `VLLMAdvisorAgent` use the same portfolio figures and instructions: concise 3-4 sentence briefing covering return, risk, diversification and concentration, specific and neutral.

## VLLMAdvisorAgent

Implement an async method conceptually:

```python
async def summarize_async(self, holdings, metrics, risk, context=None) -> str:
    ...
```

Flow:

```text
shared prompt builder
-> async inference client
-> completed inference observation
-> optional sink
-> generated text
```

The PortfolioRuntime business response continues to expose only `summary` as a string; token/telemetry data stays internal.

## PortfolioRuntime integration

Pass RequestContext into `summarize_async`. The async Advisor must be directly awaited and never consume the workflow ThreadPoolExecutor. Preserve all Phase 1 ordering, concurrency and cancellation behavior.

Canonical inference failure must propagate. Do not silently switch to Bedrock, deterministic fallback or retry when retry_count is zero.

The legacy Advisor may remain for offline compatibility, but it is not the canonical serving path.

## Lifecycle

The inference client must support async close. The future service will create one client/runtime at startup and close it at shutdown. Make lifecycle idempotent enough to avoid double-close surprises. If PortfolioRuntime owns/injects a closeable async Advisor, its close path may be extended carefully without breaking Phase 1.

## Errors

Represent useful failure classes or messages for:

- connection failure
- timeout
- non-2xx response
- malformed JSON
- missing completion choice/text

Preserve useful status/context without exposing prompts, API keys or secrets.

## Focused tests

Use `httpx.MockTransport` or equivalent; no real vLLM.

Required coverage:

1. endpoint is `/v1/chat/completions`
2. correct model/message/temperature/max_tokens/stream payload
3. generated text extraction
4. prompt/completion/total token extraction
5. model extraction
6. run/request/query correlation reaches observation sink
7. HTTP 500 propagates
8. retry_count=0 makes exactly one attempt
9. client reused across multiple calls and closes cleanly
10. delayed mock does not block event loop
11. canonical async Advisor does not consume workflow executor
12. PortfolioRuntime business response remains unchanged and does not expose inference telemetry

Run focused Phase 2 tests only unless they expose a compatibility issue requiring broader testing.

## Acceptance criteria

Phase 2 is complete when the serving workflow can use a tested async OpenAI-compatible Advisor boundary, exactly one inference call is made per workflow request, exact usage is captured from the response, failures are visible, the client is reusable/closeable, and no actual model server is required for tests.

## Out of scope

- FastAPI service
- Gateway
- real vLLM startup/model download
- Docker/GPU
- Prometheus/OpenTelemetry/Grafana
- benchmark runner
- PostgreSQL/Parquet
- cost accounting