# Demo 1 Diagrams: 100-Query C=16 Observability Walkthrough

Use this file as the visual reference during Loom Demo 1.

## Diagram 1: End-to-End Request Architecture

```mermaid
flowchart TD
    A[Benchmark / Client] -->|POST /v1/analyze| B[Gateway]
    B --> B1[Validation]
    B --> B2[Admission Control]
    B --> C[Portfolio API]
    C --> D[Portfolio Workflow]
    D --> E[MetricsAgent]
    E --> F[PriceAgent: get_history per ticker]
    D --> G[RiskAgent]
    E --> H[AdvisorAgent]
    G --> H
    H -->|OpenAI-compatible inference request| I[vLLM]
    I --> I1[Scheduler and Continuous Batching]
    I --> I2[KV Cache]
    I --> J[Qwen Open-Weight Model]
```

Key points:
- Gateway is the public request boundary.
- Portfolio API executes the supplied multi-agent workflow.
- MetricsAgent fans out to PriceAgent for holdings.
- RiskAgent computes portfolio risk.
- AdvisorAgent performs the LLM-backed summarization step.
- vLLM is the model-serving layer for scheduling, batching, KV cache, and inference execution.

## Diagram 2: Live Observability Flow

```mermaid
flowchart TD
    A[Running Application] --> B[Metrics]
    A --> C[Traces]
    A --> D[Logs]
    B --> E[Prometheus]
    C --> F[OpenTelemetry]
    F --> G[Jaeger]
    D --> H[Alloy]
    H --> I[Loki]
    E --> J[Grafana]
    I --> J
    G --> K[Per-request trace drilldown]
    J --> L[Live serving dashboards]
```

Key points:
- Prometheus answers: what is the whole system doing?
- Jaeger answers: what happened to this specific request?
- Loki stores structured logs.
- Grafana is the primary live operational view.

## Diagram 3: One Request in Jaeger

```mermaid
flowchart TD
    A[POST /v1/analyze] --> B[gateway.validation]
    B --> C[gateway.admission_wait]
    C --> D[POST /internal/analyze]
    D --> E[portfolio.workflow]
    E --> F[MetricsAgent AAPL]
    F --> G[PriceAgent.get_history AAPL]
    E --> H[MetricsAgent MSFT]
    H --> I[PriceAgent.get_history MSFT]
    E --> J[RiskAgent.assess]
    E --> K[AdvisorAgent.summarize]
    K --> L[inference.request]
```

Correlation IDs:
- `run_id`
- `request_id`
- `query_id`

Useful fields on `inference.request`:
- model
- prompt tokens
- completion tokens
- total tokens
- retry count
- request ID
- query ID
- run ID

## Diagram 4: Benchmark to Historical Analytics

```mermaid
flowchart TD
    A[Benchmark] --> B[Raw Request Results: requests.jsonl]
    A --> C[Jaeger Traces]
    A --> D[Prometheus Telemetry]
    B --> E[Analytics Collector]
    C --> E
    D --> E
    E --> F[Request Observations]
    E --> G[Execution Observations]
    E --> H[Inference Observations]
    E --> I[Resource Samples]
    F --> J[Cost and Derived Metrics]
    G --> J
    H --> J
    I --> J
    J --> K[Request Cost Attribution]
    J --> L[Agent Cost Attribution]
    K --> M[Parquet]
    L --> M
    J --> N[PostgreSQL]
    J --> O[metrics.json and report.json]
    J --> P[Static HTML and SVG Report]
    N --> Q[Historical Grafana]
```

Mental model:
- Raw data = what happened.
- Telemetry = how it executed.
- Analytics = what we concluded.

## Diagram 5: Cost Attribution Model

```mermaid
flowchart TD
    A[Machine Hourly Cost] --> B[Multiply by Measured Run Duration]
    B --> C[Total Infrastructure Cost]
    C --> D[35% CPU Pool]
    C --> E[45% Inference Pool]
    C --> F[20% Overhead Pool]
    D --> G[Exclusive Execution Work from Nested Traces]
    G --> H[PriceAgent]
    G --> I[MetricsAgent]
    G --> J[RiskAgent]
    E --> K[Weighted Token Work]
    K --> K1[prompt_tokens x 1]
    K --> K2[completion_tokens x 4]
    K --> L[Per-request Inference Attribution]
    L --> M[AdvisorAgent at Agent Level]
    F --> N[overhead_unallocated at Agent Level]
    F --> O[Request Wall-Time Weighting at Request Level]
```

Important caveat:
- The CPU rehearsal uses an illustrative synthetic cost profile.
- `35% / 45% / 20%` are configured accounting assumptions.
- `1 / 4` token weights are configurable heuristics.
- They are not claims about exact physical hardware cost.

## Diagram 6: Concurrency and vLLM Serving

```mermaid
flowchart TD
    A[100 Benchmark Queries] --> B[Client Concurrency = 16]
    B --> C[Up to 16 Requests In Flight]
    C --> D[Gateway]
    D --> E[Portfolio API]
    E --> F[Advisor Inference Requests]
    F --> G[vLLM Scheduler]
    G --> H[Running Requests]
    G --> I[Waiting Requests]
    G --> J[Continuous Batching]
    G --> K[KV Cache]
    G --> L[Preemption if Pressured]
    H --> M[Qwen Model]
    J --> M
```

Metrics to watch:
- Requests running
- Requests waiting
- Prompt throughput
- Generation throughput
- TTFT
- TPOT
- Queue latency
- KV-cache utilization
- Preemptions
- Success rate
- p95 latency

## Quick Demo Commands

Run the 100-query C=16 experiment:

```bash
deploy/experiments/phase9_observability_demo.sh run_100 16
```

Grafana:

```text
http://localhost:3000
```

Jaeger:

```text
http://localhost:16686
```

After the script prints the run ID, search Jaeger with:

```json
{"run_id":"<RUN_ID>"}
```

Generated report:

```text
results/phase8/analytics/<RUN_ID>/report/index.html
```
