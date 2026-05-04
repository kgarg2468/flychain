import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AnchorHTMLAttributes } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import HomePage from './page';
import { gateway } from '@/lib/gateway';

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
      flywheel: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        capability: {
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
        summary: {
          total_traces: 2,
          evaluated_traces: 2,
          failing_traces: 1,
          unresolved_failures: 0,
          clusters: 1,
          datasets: 1,
          training_runs: 1,
          latest_served_validation: {
            run_id: 'run_1',
            status: 'passed',
            aggregate_score: 1,
            provider: 'local-mlx',
            adapter_run_id: 'run_1',
          },
          active_adapter: {
            capability_id: 'groundedness',
            active_run_id: 'run_1',
            adapter_dir: '/tmp/adapter',
            baseline: { groundedness: 0.6 },
            candidate: { groundedness: 0.81 },
          },
          last_adapted_chat: {
            trace_id: 'trace_active',
            provider: 'local-mlx',
            model: 'mlx-community/Llama-3.2-3B-Instruct-4bit',
            ts: '2026-05-03T00:03:00+00:00',
          },
        },
        timeline: [
          { id: 'capture', label: 'Capture traces', status: 'complete', count: 2, href: '#traces' },
          { id: 'evaluate', label: 'Evaluate', status: 'complete', count: 2, href: '#traces' },
          { id: 'fail', label: 'Detect failures', status: 'complete', count: 1, href: '#failures' },
          {
            id: 'correct',
            label: 'Collect corrections',
            status: 'complete',
            count: 1,
            href: '#failures',
          },
          { id: 'cluster', label: 'Cluster', status: 'complete', count: 1, href: '#clusters' },
          {
            id: 'dataset',
            label: 'Synthesize dataset',
            status: 'complete',
            count: 1,
            href: '#datasets',
          },
          { id: 'train', label: 'Train', status: 'complete', count: 1, href: '#runs' },
          {
            id: 'validate',
            label: 'Validate served adapter',
            status: 'complete',
            count: 1,
            href: '#runs',
          },
          { id: 'promote', label: 'Promote', status: 'complete', count: 1, href: '#runs' },
          {
            id: 'serve',
            label: 'Serve active adapter',
            status: 'complete',
            count: 1,
            href: '#before-after',
          },
        ],
        failures: [
          {
            trace_id: 'trace_fail',
            project_id: 'proj-a',
            input: 'What is the refund window?',
            output: 'Refunds are available for 90 days.',
            context: '',
            tags: {},
            ts: '2026-05-03T00:00:00+00:00',
            aggregate_score: 0.2,
            failing_dimensions: ['all_claims_supported'],
            corrected_response: 'Refunds are available for 30 days.',
            correction_status: 'corrected',
            review_status: 'needs_correction',
            cluster_ids: ['groundedness-c0'],
            dataset_eligible: true,
            dimension_results: [
              {
                dimension: 'all_claims_supported',
                score: 0.2,
                passed: false,
                reason: 'unsupported',
                evaluator_type: 'deterministic',
                evaluator_source: 'deterministic:exact_match',
                ts: '2026-05-03T00:00:01+00:00',
              },
            ],
          },
        ],
        clusters: [
          {
            id: 'groundedness-c0',
            capability_id: 'groundedness',
            label: 'refund window',
            size: 1,
            trace_ids: ['trace_fail'],
            correction_coverage: { corrected: 1, total: 1 },
            dataset_eligible: true,
            latest_dataset_id: 'ds_1',
            representative_failures: [],
          },
        ],
        datasets: [
          {
            id: 'ds_1',
            capability_id: 'groundedness',
            cluster_id: 'groundedness-c0',
            method: 'sft',
            path: '/tmp/ds_1.jsonl',
            row_count: 1,
            training_run_ids: ['run_1'],
            correction_source: { human: 1, generated: 0 },
          },
        ],
        training_runs: [
          {
            id: 'run_1',
            capability_id: 'groundedness',
            recipe_id: 'sft-mlx-lora-local-3b',
            dataset_id: 'ds_1',
            dataset_path: '/tmp/ds_1.jsonl',
            status: 'promoted',
            created_at: '2026-05-03T00:01:00+00:00',
            updated_at: '2026-05-03T00:02:00+00:00',
            artifact: { backend: 'mlx-lm', adapter_dir: '/tmp/adapter', dry_run: false },
            baseline: { groundedness: 0.6 },
            candidate: { groundedness: 0.81 },
            gate_verdict: { decision: 'promote' },
            latest_comparison: { delta: 0.21 },
            served_validation: { status: 'passed', aggregate_score: 1, provider: 'local-mlx' },
            allow_backend_fallback: false,
            error: null,
            active: true,
            validation_status: 'passed',
            gate_status: 'promote',
            artifact_path: '/tmp/adapter',
          },
        ],
        jobs: [],
        active_adapter: {
          capability_id: 'groundedness',
          active: {
            capability_id: 'groundedness',
            active_run_id: 'run_1',
            adapter_dir: '/tmp/adapter',
            baseline: { groundedness: 0.6 },
            candidate: { groundedness: 0.81 },
          },
        },
        before_after: {
          run_id: 'run_1',
          replay_trace_id: 'trace_fail',
          input: 'What is the refund window?',
          baseline_output: 'Refunds are available for 90 days.',
          adapted_output: 'Refunds are available for 30 days.',
          final_verdict: 'passed',
          adapter_proof: {
            provider: 'local-mlx',
            adapter_run_id: 'run_1',
          },
          evaluator_scores: [],
        },
        traces: [],
      }),
      guidedActions: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        readiness: { min_cluster_size: 3, active_adapter_run_id: 'run_1' },
        thresholds: { min_corrected_failures: 3 },
        active_adapter: {
          capability_id: 'groundedness',
          active: {
            active_run_id: 'run_1',
            adapter_dir: '/tmp/adapter',
            baseline: { groundedness: 0.6 },
            candidate: { groundedness: 0.81 },
          },
        },
        actions: [
          {
            id: 'create_dataset:groundedness-c0',
            type: 'create_dataset',
            target_id: 'groundedness-c0',
            status: 'available',
            requires_approval: false,
            reason: 'Create an SFT dataset from human corrections only',
            blocked_reasons: [],
            preview: {
              cluster_id: 'groundedness-c0',
              included_count: 3,
              skipped_count: 0,
              method: 'sft',
            },
            default_params: { method: 'sft', generate_missing: false },
          },
          {
            id: 'start_training:ds_1',
            type: 'start_training',
            target_id: 'ds_1',
            status: 'available',
            requires_approval: true,
            reason: 'Queue one training run after inline approval',
            blocked_reasons: [],
            preview: {
              dataset_id: 'ds_1',
              row_count: 3,
              recipe_id: 'sft-mlx-lora-local-3b',
              recipe_backend: 'mlx-lm',
              allow_backend_fallback: false,
              mlx_health: { name: 'MLX server', status: 'ok', target: 'http://mlx.test' },
            },
            default_params: { recipe_id: 'sft-mlx-lora-local-3b', allow_backend_fallback: false },
          },
          {
            id: 'run_served_validation:run_1',
            type: 'run_served_validation',
            target_id: 'run_1',
            status: 'complete',
            requires_approval: false,
            reason: 'Served validation already passed',
            blocked_reasons: [],
            preview: {
              managed_replay_name: 'Managed validation: ds_1',
              served_validation_status: 'passed',
            },
            default_params: { managed_replay: true },
          },
          {
            id: 'promote_adapter:run_1',
            type: 'promote_adapter',
            target_id: 'run_1',
            status: 'complete',
            requires_approval: true,
            reason: 'Run is already active',
            blocked_reasons: [],
            preview: {
              candidate_run_id: 'run_1',
              validation_score: 1,
              validation_status: 'passed',
              current_active_adapter: { active_run_id: 'run_1' },
            },
            default_params: { replace_active: true },
          },
        ],
      }),
      executeGuidedAction: vi.fn(),
      autopilotStatus: vi.fn().mockResolvedValue({
        capability_id: 'groundedness',
        policy: {
          capability_id: 'groundedness',
          enabled: true,
          min_corrected_failures: 3,
          min_cluster_size: 3,
          allowed_training_recipes: ['sft-mlx-lora-local-3b'],
          auto_generate_corrections: false,
          allow_generated_corrections: false,
          auto_create_dataset: true,
          auto_start_training: true,
          auto_run_served_validation: true,
          auto_promote: false,
          require_promotion_approval: true,
          allow_dry_run_fallback: false,
          require_served_validation: true,
          max_training_runs_per_day: 1,
          promotion_cooldown_seconds: 86400,
          rollback_mode: 'disable_current',
          version: 2,
          updated_at: '2026-05-04T00:00:00+00:00',
        },
        readiness: {
          failures: 3,
          corrected_failures: 3,
          eligible_failures: 3,
          clusters: 1,
          datasets: 1,
          training_runs: 1,
        },
        latest_decision: {
          id: 'auto_1',
          trigger: 'served_validation_completed',
          action: 'promote_adapter',
          outcome: 'approval_required',
          reasons: ['promotion approval required'],
          target_id: 'run_1',
          job_ids: ['job_1'],
          approval_status: 'pending',
          policy_version: 2,
          input_counts: { failures: 3 },
          result: { candidate_run_id: 'run_1', validation: { aggregate_score: 1 } },
          created_at: '2026-05-04T00:01:00+00:00',
          updated_at: '2026-05-04T00:01:00+00:00',
        },
        pending_approval: {
          id: 'auto_1',
          trigger: 'served_validation_completed',
          action: 'promote_adapter',
          outcome: 'approval_required',
          reasons: ['promotion approval required'],
          target_id: 'run_1',
          job_ids: ['job_1'],
          approval_status: 'pending',
          policy_version: 2,
          input_counts: { failures: 3 },
          result: { candidate_run_id: 'run_1', validation: { aggregate_score: 1 } },
          created_at: '2026-05-04T00:01:00+00:00',
          updated_at: '2026-05-04T00:01:00+00:00',
        },
        audit: [
          {
            id: 'auto_1',
            trigger: 'served_validation_completed',
            action: 'promote_adapter',
            outcome: 'approval_required',
            reasons: ['promotion approval required'],
            target_id: 'run_1',
            job_ids: ['job_1'],
            approval_status: 'pending',
            policy_version: 2,
            input_counts: { failures: 3 },
            result: { candidate_run_id: 'run_1' },
            created_at: '2026-05-04T00:01:00+00:00',
            updated_at: '2026-05-04T00:01:00+00:00',
          },
        ],
      }),
      updateAutopilotPolicy: vi.fn(),
      runAutopilot: vi.fn(),
      approveAutopilot: vi.fn(),
      rollbackCapability: vi.fn(),
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
  beforeEach(() => {
    vi.mocked(gateway.executeGuidedAction).mockClear();
    vi.mocked(gateway.guidedActions).mockClear();
    vi.mocked(gateway.updateAutopilotPolicy).mockClear();
    vi.mocked(gateway.approveAutopilot).mockClear();
    vi.mocked(gateway.rollbackCapability).mockClear();
  });

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
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    expect(screen.getByRole('heading', { name: /active adapter/i })).toBeInTheDocument();
    expect(screen.getAllByText(/run_1/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('/tmp/adapter').length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: /flywheel status/i })).toBeInTheDocument();
    expect(screen.getByText(/capture traces/i)).toBeInTheDocument();
    expect(screen.getByText(/validate served adapter/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /failure inbox/i })).toBeInTheDocument();
    expect(screen.getByText(/unsupported/i)).toBeInTheDocument();
    expect(screen.getByText(/deterministic:exact_match/i)).toBeInTheDocument();
    expect(screen.getAllByText(/refund window/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/ds_1/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/trained/i)).toBeInTheDocument();
    expect(screen.getByText(/validated/i)).toBeInTheDocument();
    expect(screen.getAllByText(/active/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: /before \/ after/i })).toBeInTheDocument();
    expect(screen.getAllByText('Refunds are available for 90 days.').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Refunds are available for 30 days.').length).toBeGreaterThan(0);
  });

  it('renders guided actions with inline approval evidence', async () => {
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    expect(screen.getByRole('heading', { name: /guided actions/i })).toBeInTheDocument();
    expect(screen.getByText(/create_dataset/i)).toBeInTheDocument();
    expect(screen.getByText(/start_training/i)).toBeInTheDocument();
    expect(screen.getAllByText(/sft-mlx-lora-local-3b/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/fallback disabled/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/managed validation: ds_1/i)).toBeInTheDocument();
  });

  it('renders autopilot policy, pending approval, and audit evidence', async () => {
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    expect(screen.getByRole('heading', { name: /autopilot policy/i })).toBeInTheDocument();
    expect(screen.getByText(/policy enabled/i)).toBeInTheDocument();
    expect(screen.getByText(/approval required/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /autopilot audit/i })).toBeInTheDocument();
    expect(screen.getByText('served_validation_completed')).toBeInTheDocument();
    expect(screen.getAllByText('promote_adapter').length).toBeGreaterThan(0);
    expect(screen.getByText(/promotion approval required/i)).toBeInTheDocument();
  });

  it('saves autopilot policy changes and exposes approval and rollback actions', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.updateAutopilotPolicy).mockResolvedValueOnce({
      capability_id: 'groundedness',
      policy: {
        capability_id: 'groundedness',
        enabled: false,
        min_corrected_failures: 3,
        min_cluster_size: 3,
        allowed_training_recipes: ['sft-mlx-lora-local-3b'],
        auto_generate_corrections: false,
        allow_generated_corrections: false,
        auto_create_dataset: true,
        auto_start_training: true,
        auto_run_served_validation: true,
        auto_promote: false,
        require_promotion_approval: true,
        allow_dry_run_fallback: false,
        require_served_validation: true,
        max_training_runs_per_day: 1,
        promotion_cooldown_seconds: 86400,
        rollback_mode: 'disable_current',
        version: 3,
        updated_at: '2026-05-04T00:02:00+00:00',
      },
    });
    vi.mocked(gateway.approveAutopilot).mockResolvedValueOnce({
      capability_id: 'groundedness',
      active_run_id: 'run_1',
      decision: {},
    });
    vi.mocked(gateway.rollbackCapability).mockResolvedValueOnce({
      capability_id: 'groundedness',
      status: 'rolled_back',
      active: null,
      decision: {},
    });
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    await user.click(screen.getByLabelText(/enable autopilot/i));
    await user.click(screen.getByRole('button', { name: /save policy/i }));

    await waitFor(() => {
      expect(gateway.updateAutopilotPolicy).toHaveBeenCalledWith(
        'groundedness',
        expect.objectContaining({ enabled: false }),
      );
    });

    await user.click(screen.getByRole('button', { name: /approve autopilot promotion/i }));
    await waitFor(() => {
      expect(gateway.approveAutopilot).toHaveBeenCalledWith('groundedness', 'auto_1', {
        approved: true,
      });
    });

    await user.click(screen.getByRole('button', { name: /rollback active adapter/i }));
    await waitFor(() => {
      expect(gateway.rollbackCapability).toHaveBeenCalledWith('groundedness', {
        reason: 'operator rollback from dashboard',
      });
    });
  });

  it('executes the next guided dataset action through the guided API', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.executeGuidedAction).mockResolvedValueOnce({
      capability_id: 'groundedness',
      action: {
        id: 'create_dataset:groundedness-c0',
        type: 'create_dataset',
        target_id: 'groundedness-c0',
        status: 'available',
        requires_approval: false,
        reason: 'Create an SFT dataset from human corrections only',
        blocked_reasons: [],
        preview: {},
        default_params: {},
      },
      result: { dataset_id: 'ds_guided', row_count: 3 },
    });
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    await user.click(screen.getAllByRole('button', { name: /create dataset/i })[0]);

    await waitFor(() => {
      expect(gateway.executeGuidedAction).toHaveBeenCalledWith(
        'groundedness',
        'create_dataset:groundedness-c0',
        { approved: false },
      );
    });
  });

  it('requires inline approval before executing guided training', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.guidedActions).mockResolvedValueOnce({
      capability_id: 'groundedness',
      readiness: { min_cluster_size: 3, active_adapter_run_id: 'run_1' },
      thresholds: { min_corrected_failures: 3 },
      active_adapter: {
        capability_id: 'groundedness',
        active: {
          active_run_id: 'run_1',
          adapter_dir: '/tmp/adapter',
          baseline: { groundedness: 0.6 },
          candidate: { groundedness: 0.81 },
        },
      },
      actions: [
        {
          id: 'create_dataset:groundedness-c0',
          type: 'create_dataset',
          target_id: 'groundedness-c0',
          status: 'complete',
          requires_approval: false,
          reason: 'Dataset already exists for this cluster',
          blocked_reasons: [],
          preview: { downstream_dataset_ids: ['ds_1'] },
          default_params: {},
        },
        {
          id: 'start_training:ds_1',
          type: 'start_training',
          target_id: 'ds_1',
          status: 'available',
          requires_approval: true,
          reason: 'Queue one training run after inline approval',
          blocked_reasons: [],
          preview: {
            dataset_id: 'ds_1',
            row_count: 3,
            recipe_id: 'sft-mlx-lora-local-3b',
            recipe_backend: 'mlx-lm',
            allow_backend_fallback: false,
            mlx_health: { name: 'MLX server', status: 'ok' },
          },
          default_params: { recipe_id: 'sft-mlx-lora-local-3b', allow_backend_fallback: false },
        },
      ],
    });
    vi.mocked(gateway.executeGuidedAction).mockResolvedValueOnce({
      capability_id: 'groundedness',
      action: {
        id: 'start_training:ds_1',
        type: 'start_training',
        target_id: 'ds_1',
        status: 'available',
        requires_approval: true,
        reason: 'Queue one training run after inline approval',
        blocked_reasons: [],
        preview: {},
        default_params: {},
      },
      result: { run_id: 'run_guided', status: 'queued' },
    });
    render(await HomePage({ searchParams: { tab: 'capabilities', capability: 'groundedness' } }));

    await user.click(screen.getByRole('button', { name: /review approval/i }));

    expect(screen.getByRole('heading', { name: /training approval/i })).toBeInTheDocument();
    expect(screen.getAllByText(/fallback disabled/i).length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: /approve training/i }));

    await waitFor(() => {
      expect(gateway.executeGuidedAction).toHaveBeenCalledWith(
        'groundedness',
        'start_training:ds_1',
        { approved: true },
      );
    });
  });
});
