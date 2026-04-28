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
    },
  };
});

describe('HomePage', () => {
  it('renders capability scorecards from gateway snapshots', async () => {
    render(await HomePage());

    expect(screen.getByText('Groundedness')).toBeInTheDocument();
    expect(screen.getByText('0.81')).toBeInTheDocument();
    expect(screen.getByText('+0.210')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /chat/i })).toHaveAttribute('href', '/chat');
  });
});
