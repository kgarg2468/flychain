import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { CapabilityControls } from './controls';
import { gateway } from '@/lib/gateway';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

vi.mock('@/lib/gateway', async () => {
  const actual = await vi.importActual<typeof import('@/lib/gateway')>('@/lib/gateway');
  return {
    ...actual,
    gateway: {
      ...actual.gateway,
      clusterRun: vi.fn().mockResolvedValue({ clusters: [], noise_trace_ids: [] }),
      createReplaySet: vi.fn().mockResolvedValue({}),
      updateReplaySet: vi.fn().mockResolvedValue({}),
      synthesizeDataset: vi.fn().mockResolvedValue({ id: 'ds_1', row_count: 1 }),
      createTrainingRun: vi.fn().mockResolvedValue({ id: 'run_1', status: 'queued' }),
      abCompare: vi.fn().mockResolvedValue({ delta: 0.2, sample_count: 1 }),
      applyGate: vi.fn().mockResolvedValue({ id: 'run_1', status: 'gate-queued' }),
      activateAdapter: vi.fn().mockResolvedValue({ active_run_id: 'run_1' }),
      deactivateAdapter: vi.fn().mockResolvedValue(undefined),
    },
  };
});

describe('CapabilityControls', () => {
  it('clusters selected failures without raw JSON entry', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <CapabilityControls
        capabilityId="groundedness"
        clusters={[]}
        datasets={[]}
        runs={[]}
        recipes={[]}
        failures={[
          {
            trace_id: 'trace-1',
            project_id: 'demo',
            input: 'What is the refund window?',
            output: 'Refunds are 90 days.',
            context: '',
            tags: { task: 'rag' },
            ts: '2026-04-22T00:00:00Z',
            aggregate_score: 0.2,
            failing_dimensions: ['all_claims_supported'],
            corrected_response: 'Refunds are 30 days.',
          },
        ]}
        replaySets={[]}
      />,
    );

    expect(container.querySelector('textarea')).toBeNull();

    await user.click(screen.getByLabelText(/select failure trace-1/i));
    await user.click(screen.getByRole('button', { name: /run clustering/i }));

    expect(gateway.clusterRun).toHaveBeenCalledWith('groundedness', {
      failure_ids: ['trace-1'],
      min_cluster_size: 3,
      summarize: true,
    });
  });

  it('edits and saves a replay set through structured row fields', async () => {
    const user = userEvent.setup();
    render(
      <CapabilityControls
        capabilityId="groundedness"
        clusters={[]}
        datasets={[]}
        runs={[]}
        recipes={[]}
        failures={[]}
        replaySets={[
          {
            id: 'replay_1',
            capability_id: 'groundedness',
            name: 'Held-out',
            created_at: '2026-04-22T00:00:00Z',
            updated_at: '2026-04-22T00:00:00Z',
            rows: [
              {
                trace_id: 't1',
                project_id: 'demo',
                input: 'Question 1',
                context: 'Source',
                baseline_output: 'Wrong',
                candidate_output: 'Better',
                tags: { task: 'rag' },
              },
            ],
          },
        ]}
      />,
    );

    await user.selectOptions(screen.getByLabelText(/^Replay set$/i), 'replay_1');
    await user.clear(screen.getByLabelText(/replay set name/i));
    await user.type(screen.getByLabelText(/replay set name/i), 'Held-out v2');
    await user.clear(screen.getByLabelText(/candidate output 1/i));
    await user.type(screen.getByLabelText(/candidate output 1/i), 'Much better');
    await user.click(screen.getByRole('button', { name: /save replay set/i }));

    expect(gateway.updateReplaySet).toHaveBeenCalledWith('groundedness', 'replay_1', {
      name: 'Held-out v2',
      rows: [
        {
          trace_id: 't1',
          project_id: 'demo',
          input: 'Question 1',
          context: 'Source',
          baseline_output: 'Wrong',
          candidate_output: 'Much better',
          tags: { task: 'rag' },
        },
      ],
    });
  });
});
