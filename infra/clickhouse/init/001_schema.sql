-- FlyChain ClickHouse schema (initial)
-- Runs on first container start via /docker-entrypoint-initdb.d.

CREATE DATABASE IF NOT EXISTS flychain;

-- Raw trace events from the gateway.
CREATE TABLE IF NOT EXISTS flychain.traces (
    trace_id        String,
    span_id         String,
    parent_span_id  String,
    project_id      String,
    capability_ids  Array(String),
    provider        LowCardinality(String),
    model           LowCardinality(String),
    method          LowCardinality(String),
    request         String CODEC(ZSTD(3)),
    response        String CODEC(ZSTD(3)),
    prompt_tokens   UInt32,
    completion_tokens UInt32,
    total_tokens    UInt32,
    cost_usd        Float64,
    latency_ms      UInt32,
    status          LowCardinality(String),
    error           String,
    tags            Map(String, String),
    ts              DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (project_id, ts, trace_id)
TTL toDateTime(ts) + INTERVAL 180 DAY;

-- Per-capability eval scores for each trace.
CREATE TABLE IF NOT EXISTS flychain.eval_scores (
    trace_id        String,
    project_id      String,
    capability_id   String,
    dimension       LowCardinality(String),
    score           Float32,
    passed          UInt8,
    reason          String CODEC(ZSTD(3)),
    judge_model     LowCardinality(String),
    evaluator_type  LowCardinality(String),
    evaluator_source LowCardinality(String),
    ts              DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (project_id, capability_id, ts, trace_id);

-- Embeddings of failed traces per capability, used for HDBSCAN clustering.
CREATE TABLE IF NOT EXISTS flychain.failure_embeddings (
    trace_id        String,
    project_id      String,
    capability_id   String,
    embedding_model LowCardinality(String),
    embedding       Array(Float32),
    ts              DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (project_id, capability_id, ts, trace_id);

-- End-user feedback tied back to a trace.
CREATE TABLE IF NOT EXISTS flychain.feedback (
    feedback_id     String,
    trace_id        String,
    project_id      String,
    score           Int8,
    thumb           Enum8('up' = 1, 'down' = -1, 'none' = 0),
    comment         String CODEC(ZSTD(3)),
    corrected_response String CODEC(ZSTD(3)),
    ts              DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (project_id, ts, feedback_id);
