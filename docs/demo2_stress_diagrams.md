# Demo 2 Diagrams: 1000-Query C=100 Stress Test

Use this file as the visual reference for the later high-concurrency stress-test Loom.

## Diagram 1: Stress-Test Request Pressure

```mermaid
flowchart TD
    A[1000 Benchmark Queries] --> B[Client Concurrency = 100]
    B --> C[Up to 100 Requests In Flight]
    C --> D[Gateway]
    D --> E[Portfolio API]
    E --> F[Advisor Inference Requests]
    F --> G[vLLM Scheduler]
    G --> H[Running Sequences]
    G --> I[Waiting Sequences]
    G --> J[Continuous Batching]
    G --> K[KV Cache]
    G --> L[Preemption if Capacity Is Pressured]
    H --> M[Qwen Model]
    J --> M
```

Key point:
- Client concurrency is intentionally much higher than the normal demo workload so the test can reveal queueing, saturation, tail-latency growth, and failure behavior.

## Diagram 2: What Saturation Looks Like

```mermaid
flowchart LR
    A[Increase Client Concurrency] --> B[More Requests In Flight]
    B --> C{Server Has Spare Capacity?}
    C -->|Yes| D[Throughput Increases]
    C -->|No| E[Requests Wait]
    E --> F[Queue Latency Increases]
    F --> G[TTFT Increases]
    G --> H[p95 / p99 E2E Latency Increases]
    H --> I{Resource Pressure Severe?}
    I -->|No| J[Stable but Saturated]
    I -->|Yes| K[Preemption / Timeouts / Failures]
```

## Diagram 3: vLLM Signals to Watch

```mermaid
flowchart TD
    A[vLLM Under Load] --> B[Requests Running]
    A --> C[Requests Waiting]
    A --> D[Prompt Throughput]
    A --> E[Generation Throughput]
    A --> F[TTFT]
    A --> G[TPOT]
    A --> H[Queue Latency]
    A --> I[KV-Cache Utilization]
    A --> J[Preemptions]
    A --> K[E2E p95 / p99]
    A --> L[Success Rate / Timeouts]
```

Interpretation:
- Throughput rising with low waiting means useful parallelism remains.
- Waiting and queue latency rising indicate scheduler pressure.
- TTFT usually reflects queueing plus prefill pressure.
- TPOT reflects decode efficiency under load.
- High KV-cache usage and preemptions indicate memory pressure.
- Tail latency and failure rate determine whether the system remains usable.

## Diagram 4: Stress-Test Analysis Flow

```mermaid
flowchart TD
    A[1000-Query C=100 Benchmark] --> B[Raw Request Results]
    A --> C[Prometheus Serving Telemetry]
    A --> D[Jaeger Traces]
    B --> E[Analytics Collector]
    C --> E
    D --> E
    E --> F[Success and Failure Analysis]
    E --> G[Latency Distribution]
    E --> H[Queue and Serving Analysis]
    E --> I[Cost per Query]
    E --> J[Agent Cost Attribution]
    F --> K[Stress-Test Report]
    G --> K
    H --> K
    I --> K
    J --> K
```

## Demo 2 Questions to Answer

1. How many requests can the system keep actively running?
2. When do requests begin waiting?
3. Does throughput continue improving at C=100?
4. How much do TTFT and p95/p99 latency increase?
5. Does KV-cache utilization become significant?
6. Are there preemptions?
7. Do requests time out or fail?
8. Is the bottleneck Gateway admission, workflow execution, or inference serving?

## Stress-Test Command

Once the 1000-query runner is finalized, the intended interface is:

```bash
deploy/experiments/phase9_observability_demo.sh run_1000 100
```

The resulting report follows the same run-scoped structure:

```text
results/phase8/analytics/<RUN_ID>/report/index.html
```
