import { render, screen } from '@testing-library/react';
import type { AnchorHTMLAttributes } from 'react';
import { describe, expect, it, vi } from 'vitest';

import HomePage from './page';

vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={typeof href === 'string' ? href : '#'} {...props}>
      {children}
    </a>
  ),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/gateway', async () => {
  const actual = await vi.importActual<typeof import('@/lib/gateway')>('@/lib/gateway');
  return {
    ...actual,
    gateway: {
      ...actual.gateway,
      listCapabilities: vi.fn().mockResolvedValue([
        {
          id: 'groundedness',
          name: 'Groundedness',
          description: 'Ensure answers stay supported by retrieved context.',
          eval_dimensions: [
            { id: 'all_claims_supported', description: 'x', judge_prompt_ref: null, weight: 1 },
          ],
          slice_rules: [],
          eligible_methods: ['sft'],
          recipe_refs: ['sft-mlx-lora'],
          promotion_gate: { threshold: 0.05, max_other_regression: 0.02 },
          metadata: {},
        },
      ]),
      scorecard: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        sample_count: 4,
        aggregate_score: 0.81,
        dimensions: [],
      }),
      activeAdapter: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        active: {
          active_run_id: 'run_1',
          adapter_dir: '/tmp/adapter',
          baseline: { groundedness: 0.6 },
          candidate: { groundedness: 0.81 },
        },
      }),
      getCapability: vi.fn().mockResolvedValue({
        id: 'groundedness',
        name: 'Groundedness',
        description: 'Ensure answers stay supported by retrieved context.',
        eval_dimensions: [
          { id: 'all_claims_supported', description: 'x', judge_prompt_ref: null, weight: 1 },
        ],
        slice_rules: [],
        eligible_methods: ['sft'],
        recipe_refs: ['sft-mlx-lora'],
        promotion_gate: { threshold: 0.05, max_other_regression: 0.02 },
        metadata: {},
      }),
      clusters: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        clusters: [],
        noise_trace_ids: [],
      }),
      datasets: vi.fn().mockResolvedValue({ datasets: [] }),
      trainingRuns: vi.fn().mockResolvedValue({ runs: [] }),
      jobs: vi.fn().mockResolvedValue({
        jobs: [
          {
            id: 'job_1',
            type: 'served_validation',
            status: 'failed',
            created_at: '2026-04-22T00:00:00+00:00',
            updated_at: '2026-04-22T00:00:01+00:00',
            capability_id: 'groundedness',
            run_id: 'run_1',
            retry_count: 0,
            max_retries: 1,
            trace_ids: [],
            error: 'served validation failed',
          },
        ],
      }),
      retryJob: vi.fn(),
      failures: vi.fn().mockResolvedValue({ capability_id: 'groundedness', failures: [] }),
      replaySets: vi.fn().mockResolvedValue({ replay_sets: [] }),
      recipes: vi.fn().mockResolvedValue({ recipes: [] }),
      getSettings: vi.fn().mockResolvedValue({
        settings: {
          judge_model: 'llama3.2:3b',
          judge_provider: 'local-ollama',
          embedding_model: 'nomic-embed-text',
          min_cluster_size: 3,
          auto_eval_new_traces: false,
          auto_cluster_failures: false,
        },
        openai_configured: false,
        anthropic_configured: false,
        runtime: {
          env: 'local',
          ollama_url: 'http://localhost:11434',
          clickhouse_url: 'http://localhost:8123',
          postgres_url: 'postgresql://localhost:5432',
          redis_url: 'redis://localhost:6379/0',
          data_dir: '/tmp/flychain-data',
        },
      }),
      traces: vi.fn().mockResolvedValue({ traces: [], total: 0, limit: 100, offset: 0 }),
    },
  };
});

describe('HomePage', () => {
  it('renders capability scorecards from gateway snapshots', async () => {
    render(await HomePage({ searchParams: { tab: 'capabilities' } }));

    expect(screen.getByText('Groundedness')).toBeInTheDocument();
    expect(screen.getByText('0.81')).toBeInTheDocument();
    expect(screen.getByText('+0.210')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /chat/i })).toHaveAttribute('href', '/?tab=chat');
    expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute(
      'href',
      '/?tab=settings',
    );
  });

  it('renders the requested workspace tab from the URL state', async () => {
    render(await HomePage({ searchParams: { tab: 'chat' } }));

    expect(screen.getByRole('heading', { name: /chat/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/message/i)).toBeInTheDocument();
  });

  it('renders the Jobs tab with recent job status', async () => {
    render(await HomePage({ searchParams: { tab: 'jobs' } }));

    expect(screen.getByRole('heading', { name: /jobs/i })).toBeInTheDocument();
    expect(screen.getByText('job_1')).toBeInTheDocument();
    expect(screen.getByText('served_validation')).toBeInTheDocument();
    expect(screen.getByText('served validation failed')).toBeInTheDocument();
  });

  it('renders active adapter details in selected capability detail', async () => {
    render(
      await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }),
    );

    expect(screen.getByRole('heading', { name: /active adapter/i })).toBeInTheDocument();
    expect(screen.getByText(/run_1/i)).toBeInTheDocument();
    expect(screen.getByText('/tmp/adapter')).toBeInTheDocument();
  });
});
