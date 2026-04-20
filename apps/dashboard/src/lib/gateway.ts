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

export interface DatasetEntry {
  id: string;
  capability_id: string;
  cluster_id: string | null;
  method: string;
  path: string;
  row_count: number;
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

function gatewayBaseUrl(): string {
  const fromServer = process.env.FLYCHAIN_GATEWAY_URL;
  if (fromServer) return fromServer;
  return 'http://localhost:8080';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${gatewayBaseUrl()}${path}`, {
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
  return (await res.json()) as T;
}

export const gateway = {
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
  async clusters(id: string): Promise<ClustersResponse> {
    return request<ClustersResponse>(`/v1/capabilities/${encodeURIComponent(id)}/clusters`);
  },
  async datasets(id: string): Promise<{ datasets: DatasetEntry[] }> {
    return request<{ datasets: DatasetEntry[] }>(
      `/v1/capabilities/${encodeURIComponent(id)}/datasets`,
    );
  },
  async trainingRuns(capabilityId?: string): Promise<{ runs: TrainingRunRow[] }> {
    const qs = capabilityId ? `?capability_id=${encodeURIComponent(capabilityId)}` : '';
    return request<{ runs: TrainingRunRow[] }>(`/v1/training-runs${qs}`);
  },
  async activeAdapter(id: string): Promise<ActiveAdapter> {
    return request<ActiveAdapter>(`/v1/capabilities/${encodeURIComponent(id)}/active-adapter`);
  },
};
