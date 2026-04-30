/**
 * Lightweight client for the FlyChain gateway, used by the dashboard.
 * All requests are proxied through Next.js API routes (see src/app/api/...)
 * so that the browser always talks to same-origin.
 */

export interface EvalDimension {
  id: string;
  description: string;
  judge_prompt_ref: string | null;
  weight: number;
}

export interface SliceRule {
  type: 'tag' | 'regex' | 'semantic';
  value: string;
  negate: boolean;
}

export interface PromotionGate {
  threshold: number;
  max_other_regression: number;
}

export interface CapabilitySpec {
  id: string;
  name: string;
  description: string;
  eval_dimensions: EvalDimension[];
  slice_rules: SliceRule[];
  eligible_methods: string[];
  recipe_refs: string[];
  promotion_gate: PromotionGate;
  metadata: Record<string, string>;
}

export interface InterviewQuestion {
  id: string;
  question: string;
}

export interface DimensionSummary {
  dimension: string;
  mean_score: number;
  sample_count: number;
  weight: number;
}

export interface Scorecard {
  capability_id: string;
  sample_count: number;
  aggregate_score: number | null;
  dimensions: DimensionSummary[];
}

export interface ClusterSummary {
  id: string;
  capability_id: string;
  label: string;
  size: number;
  trace_ids: string[];
}

export interface ClustersResponse {
  capability_id: string;
  clusters: ClusterSummary[];
  noise_trace_ids: string[];
}

export interface TraceRow {
  trace_id: string;
  project_id: string;
  provider: string;
  model: string;
  method: string;
  request: Record<string, unknown>;
  response: Record<string, unknown>;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: string;
  error: string;
  tags: Record<string, string>;
  ts: string;
}

export interface TraceListResponse {
  traces: TraceRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface DatasetEntry {
  id: string;
  capability_id: string;
  cluster_id: string | null;
  method: string;
  path: string;
  row_count: number;
}

export interface FailureRow {
  trace_id: string;
  project_id: string;
  input: string;
  output: string;
  context: string;
  tags: Record<string, string>;
  ts: string;
  aggregate_score: number | null;
  failing_dimensions: string[];
  corrected_response: string | null;
}

export interface ReplaySetRow {
  trace_id: string;
  project_id?: string;
  input: string;
  context?: string;
  baseline_output: string;
  candidate_output: string;
  tags?: Record<string, string>;
}

export interface ReplaySetRecord {
  id: string;
  capability_id: string;
  name: string;
  rows: ReplaySetRow[];
  created_at: string;
  updated_at: string;
}

export interface TrainingRunRow {
  id: string;
  capability_id: string;
  recipe_id: string;
  dataset_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  artifact: Record<string, unknown> | null;
  baseline: Record<string, number>;
  candidate: Record<string, number>;
  gate_verdict: Record<string, unknown> | null;
  latest_comparison: {
    replay_set_id: string | null;
    baseline: { aggregate_score: number };
    candidate: { aggregate_score: number };
    delta: number;
    ts: string;
  } | null;
  allow_backend_fallback: boolean;
  error: string | null;
}

export interface ActiveAdapter {
  capability_id: string;
  active: {
    active_run_id: string;
    adapter_dir: string;
    baseline: Record<string, number>;
    candidate: Record<string, number>;
  } | null;
}

export interface RecipeRow {
  id: string;
  base_model: string;
  method: string;
  backend: string;
  description: string;
  promotion_threshold: number;
  max_other_regression: number;
}

export interface RuntimeSettings {
  judge_model: string;
  embedding_model: string;
  min_cluster_size: number;
  auto_eval_new_traces: boolean;
  auto_cluster_failures: boolean;
}

export interface SettingsPayload {
  settings: RuntimeSettings;
  openai_configured: boolean;
  anthropic_configured: boolean;
  runtime?: {
    env: string;
    ollama_url: string;
    mlx_server_url?: string | null;
    clickhouse_url: string;
    postgres_url: string;
    redis_url: string;
    data_dir: string;
  };
}

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | string;
  content: string | Array<Record<string, unknown>> | null;
}

export interface ChatCompletionUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatCompletionChoice {
  index?: number;
  finish_reason?: string | null;
  message: ChatMessage & Record<string, unknown>;
}

export interface ChatCompletionResponse {
  id?: string;
  object?: string;
  created?: number;
  model?: string;
  choices: ChatCompletionChoice[];
  usage?: ChatCompletionUsage;
  latency_ms?: number;
  [key: string]: unknown;
}

export interface ChatCompletionArgs {
  model: string;
  messages: ChatMessage[];
  projectId: string;
  capabilityIds?: string[];
  tags?: string;
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
}

export interface ChatCompletionResult {
  response: ChatCompletionResponse;
  traceId: string | null;
  activeAdapter?: {
    runId: string;
    capabilityId: string;
    provider: string | null;
    model: string | null;
  };
}

function gatewayBaseUrl(): string {
  const fromServer = process.env.FLYCHAIN_GATEWAY_URL;
  if (fromServer) return fromServer;
  return 'http://localhost:8080';
}

function requestUrl(path: string): string {
  if (typeof window !== 'undefined') {
    return `/api/gateway${path}`;
  }
  return `${gatewayBaseUrl()}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(requestUrl(path), {
    cache: 'no-store',
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    throw new Error(`gateway ${res.status}: ${await res.text()}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const gateway = {
  async chatCompletion(args: ChatCompletionArgs): Promise<ChatCompletionResult> {
    const { projectId, capabilityIds = [], tags, ...body } = args;
    const headers: Record<string, string> = {
      'content-type': 'application/json',
      'x-flychain-project': projectId,
    };
    if (capabilityIds.length > 0) {
      headers['x-flychain-capabilities'] = capabilityIds.join(',');
    }
    if (tags?.trim()) {
      headers['x-flychain-tags'] = tags.trim();
    }

    const res = await fetch('/api/chat/completions', {
      method: 'POST',
      headers,
      body: JSON.stringify({ ...body, stream: false }),
    });
    if (!res.ok) {
      throw new Error(`gateway ${res.status}: ${await res.text()}`);
    }
    const runId = res.headers.get('x-flychain-active-adapter-run-id');
    const capabilityId = res.headers.get('x-flychain-active-adapter-capability-id');
    return {
      response: (await res.json()) as ChatCompletionResponse,
      traceId: res.headers.get('x-flychain-trace-id'),
      activeAdapter:
        runId && capabilityId
          ? {
              runId,
              capabilityId,
              provider: res.headers.get('x-flychain-provider'),
              model: res.headers.get('x-flychain-model'),
            }
          : undefined,
    };
  },
  async listCapabilities(): Promise<CapabilitySpec[]> {
    const body = await request<{ capabilities: CapabilitySpec[] }>('/v1/capabilities');
    return body.capabilities;
  },
  async listTemplates(): Promise<CapabilitySpec[]> {
    const body = await request<{ templates: CapabilitySpec[] }>('/v1/capabilities/templates');
    return body.templates;
  },
  async getCapability(id: string): Promise<CapabilitySpec> {
    return request<CapabilitySpec>(`/v1/capabilities/${encodeURIComponent(id)}`);
  },
  async createFromTemplate(args: {
    template_id: string;
    id?: string;
    name?: string;
    overwrite?: boolean;
  }): Promise<CapabilitySpec> {
    return request<CapabilitySpec>('/v1/capabilities/from-template', {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async compileSpec(args: {
    description: string;
    answers?: Record<string, string>;
  }): Promise<{ spec: CapabilitySpec; provider: string; model: string }> {
    return request('/v1/capabilities/compiler/compile', {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async interviewQuestions(
    description: string,
  ): Promise<{ questions: InterviewQuestion[]; provider: string; model: string }> {
    return request('/v1/capabilities/compiler/questions', {
      method: 'POST',
      body: JSON.stringify({ description }),
    });
  },
  async persistSpec(spec: CapabilitySpec): Promise<CapabilitySpec> {
    return request<CapabilitySpec>('/v1/capabilities', {
      method: 'POST',
      body: JSON.stringify(spec),
    });
  },
  async scorecard(id: string): Promise<Scorecard> {
    return request<Scorecard>(`/v1/capabilities/${encodeURIComponent(id)}/scorecard`);
  },
  async traces(args?: {
    project_id?: string;
    capability_id?: string;
    status?: string;
    provider?: string;
    limit?: number;
    offset?: number;
  }): Promise<TraceListResponse> {
    const search = new URLSearchParams();
    if (args?.project_id) search.set('project_id', args.project_id);
    if (args?.capability_id) search.set('capability_id', args.capability_id);
    if (args?.status) search.set('status', args.status);
    if (args?.provider) search.set('provider', args.provider);
    if (args?.limit !== undefined) search.set('limit', String(args.limit));
    if (args?.offset !== undefined) search.set('offset', String(args.offset));
    const qs = search.toString();
    return request<TraceListResponse>(`/v1/traces${qs ? `?${qs}` : ''}`);
  },
  async clusters(id: string): Promise<ClustersResponse> {
    return request<ClustersResponse>(`/v1/capabilities/${encodeURIComponent(id)}/clusters`);
  },
  async failures(id: string): Promise<{ capability_id: string; failures: FailureRow[] }> {
    return request<{ capability_id: string; failures: FailureRow[] }>(
      `/v1/capabilities/${encodeURIComponent(id)}/failures`,
    );
  },
  async clusterRun(
    id: string,
    args: {
      failures?: Array<{
        trace_id: string;
        project_id?: string;
        input: string;
        output: string;
        context?: string;
        corrected_response?: string;
        tags?: Record<string, string>;
      }>;
      failure_ids?: string[];
      min_cluster_size?: number;
      summarize?: boolean;
    },
  ): Promise<ClustersResponse> {
    return request<ClustersResponse>(`/v1/capabilities/${encodeURIComponent(id)}/cluster-run`, {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async datasets(id: string): Promise<{ datasets: DatasetEntry[] }> {
    return request<{ datasets: DatasetEntry[] }>(
      `/v1/capabilities/${encodeURIComponent(id)}/datasets`,
    );
  },
  async synthesizeDataset(
    id: string,
    args: {
      cluster?: {
        id: string;
        capability_id: string;
        label: string;
        size: number;
        trace_ids: string[];
      };
      cluster_id?: string;
      failures?: Array<{
        trace_id: string;
        project_id?: string;
        input: string;
        output: string;
        context?: string;
        corrected_response?: string;
        tags?: Record<string, string>;
      }>;
      method: string;
      generate_missing?: boolean;
    },
  ): Promise<DatasetEntry> {
    return request<DatasetEntry>(`/v1/capabilities/${encodeURIComponent(id)}/synthesize-dataset`, {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async replaySets(id: string): Promise<{ replay_sets: ReplaySetRecord[] }> {
    return request<{ replay_sets: ReplaySetRecord[] }>(
      `/v1/capabilities/${encodeURIComponent(id)}/replay-sets`,
    );
  },
  async createReplaySet(
    id: string,
    args: { name: string; rows: ReplaySetRow[] },
  ): Promise<ReplaySetRecord> {
    return request<ReplaySetRecord>(`/v1/capabilities/${encodeURIComponent(id)}/replay-sets`, {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async updateReplaySet(
    id: string,
    replaySetId: string,
    args: { name: string; rows: ReplaySetRow[] },
  ): Promise<ReplaySetRecord> {
    return request<ReplaySetRecord>(
      `/v1/capabilities/${encodeURIComponent(id)}/replay-sets/${encodeURIComponent(replaySetId)}`,
      {
        method: 'PUT',
        body: JSON.stringify(args),
      },
    );
  },
  async recipes(): Promise<{ recipes: RecipeRow[] }> {
    return request<{ recipes: RecipeRow[] }>('/v1/recipes');
  },
  async trainingRuns(capabilityId?: string): Promise<{ runs: TrainingRunRow[] }> {
    const qs = capabilityId ? `?capability_id=${encodeURIComponent(capabilityId)}` : '';
    return request<{ runs: TrainingRunRow[] }>(`/v1/training-runs${qs}`);
  },
  async createTrainingRun(args: {
    capability_id: string;
    recipe_id: string;
    dataset_id: string;
    baseline?: Record<string, number>;
    allow_backend_fallback?: boolean;
  }): Promise<TrainingRunRow> {
    return request<TrainingRunRow>('/v1/training-runs', {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async abCompare(
    id: string,
    args: {
      replay?: Array<{
        trace_id: string;
        project_id?: string;
        input: string;
        context?: string;
        baseline_output: string;
        candidate_output: string;
        tags?: Record<string, string>;
      }>;
      replay_set_id?: string;
      run_id?: string;
    },
  ): Promise<{
    capability_id: string;
    sample_count: number;
    baseline: { aggregate_score: number; scores: Array<Record<string, unknown>> };
    candidate: { aggregate_score: number; scores: Array<Record<string, unknown>> };
    delta: number;
  }> {
    return request(`/v1/capabilities/${encodeURIComponent(id)}/ab-compare`, {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async applyGate(
    runId: string,
    args: { candidate?: Record<string, number>; baseline?: Record<string, number> },
  ): Promise<TrainingRunRow> {
    return request(`/v1/training-runs/${encodeURIComponent(runId)}/apply-gate`, {
      method: 'POST',
      body: JSON.stringify(args),
    });
  },
  async activeAdapter(id: string): Promise<ActiveAdapter> {
    return request<ActiveAdapter>(`/v1/capabilities/${encodeURIComponent(id)}/active-adapter`);
  },
  async activateAdapter(id: string, runId: string): Promise<{ active_run_id: string }> {
    return request(`/v1/capabilities/${encodeURIComponent(id)}/active-adapter`, {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    });
  },
  async deactivateAdapter(id: string): Promise<void> {
    await request(`/v1/capabilities/${encodeURIComponent(id)}/active-adapter`, {
      method: 'DELETE',
    });
  },
  async getSettings(): Promise<SettingsPayload> {
    return request<SettingsPayload>('/v1/settings');
  },
  async updateSettings(args: Partial<RuntimeSettings>): Promise<SettingsPayload> {
    return request<SettingsPayload>('/v1/settings', {
      method: 'PUT',
      body: JSON.stringify(args),
    });
  },
};
