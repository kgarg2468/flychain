import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AnchorHTMLAttributes } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatClient } from './client';
import { gateway, type CapabilitySpec } from '@/lib/gateway';

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
      chatCompletion: vi.fn(),
      traceEvals: vi.fn(),
    },
  };
});

const capabilities: CapabilitySpec[] = [
  {
    id: 'groundedness',
    name: 'Groundedness',
    description: 'Answers should stay supported by context.',
    eval_dimensions: [],
    slice_rules: [],
    eligible_methods: ['sft'],
    recipe_refs: [],
    promotion_gate: { threshold: 0.05, max_other_regression: 0.02 },
    metadata: {},
  },
];

describe('ChatClient', () => {
  beforeEach(() => {
    vi.mocked(gateway.chatCompletion).mockReset();
    vi.mocked((gateway as typeof gateway & { traceEvals: ReturnType<typeof vi.fn> }).traceEvals)
      .mockReset()
      .mockResolvedValue({
        eval_status: 'pending',
        scores: [],
      });
  });

  it('renders the default chat controls and composer', () => {
    render(<ChatClient capabilities={capabilities} loadError={null} />);

    expect(screen.getByLabelText(/model/i)).toHaveValue('local-ollama:llama3.2:3b');
    expect(screen.getByLabelText(/project/i)).toHaveValue('default');
    expect(screen.getByLabelText(/capability/i)).toHaveValue('groundedness');
    expect(screen.getByLabelText(/tags/i)).toHaveValue('source=dashboard-chat');
    expect(screen.getByLabelText(/message/i)).toBeInTheDocument();
  });

  it('sends the message through the gateway helper and renders response metadata', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.chatCompletion).mockResolvedValue({
      response: {
        id: 'chatcmpl_1',
        object: 'chat.completion',
        created: 1,
        model: 'llama3.2:3b',
        choices: [
          {
            index: 0,
            finish_reason: 'stop',
            message: { role: 'assistant', content: 'Use the 30 day refund window.' },
          },
        ],
        usage: { prompt_tokens: 8, completion_tokens: 7, total_tokens: 15 },
      },
      traceId: 'trace_123',
      activeAdapter: {
        runId: 'run_mlx',
        capabilityId: 'groundedness',
        provider: 'local-mlx',
        model: 'mlx-community/Llama-3.2-3B-Instruct-4bit',
      },
    });
    vi.mocked(
      (gateway as typeof gateway & { traceEvals: ReturnType<typeof vi.fn> }).traceEvals,
    ).mockResolvedValue({
      trace_id: 'trace_123',
      capability_id: 'groundedness',
      eval_status: 'passed',
      passed: true,
      aggregate_score: 1,
      failure_status: 'passing',
      scores: [
        {
          dimension: 'all_claims_supported',
          score: 1,
          passed: true,
          reason: 'exact match',
          evaluator_type: 'deterministic',
          evaluator_source: 'deterministic:exact_match',
          ts: '2026-05-03T00:00:00+00:00',
        },
      ],
    });

    render(<ChatClient capabilities={capabilities} loadError={null} />);

    await user.type(screen.getByLabelText(/message/i), 'What is the refund window?');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(gateway.chatCompletion).toHaveBeenCalledWith({
      model: 'local-ollama:llama3.2:3b',
      messages: [{ role: 'user', content: 'What is the refund window?' }],
      projectId: 'default',
      capabilityIds: ['groundedness'],
      tags: 'source=dashboard-chat',
    });
    expect(
      (gateway as typeof gateway & { traceEvals: ReturnType<typeof vi.fn> }).traceEvals,
    ).toHaveBeenCalledWith('trace_123', 'groundedness');
    expect(await screen.findByText('Use the 30 day refund window.')).toBeInTheDocument();
    const assistantMessage = screen.getByText('Use the 30 day refund window.').closest('article');
    expect(within(assistantMessage as HTMLElement).getByText(/trace_123/i)).toBeInTheDocument();
    expect(within(assistantMessage as HTMLElement).getByText(/15 tokens/i)).toBeInTheDocument();
    expect(
      within(assistantMessage as HTMLElement).getByText(/adapter run_mlx/i),
    ).toBeInTheDocument();
    expect(within(assistantMessage as HTMLElement).getByText(/local-mlx/i)).toBeInTheDocument();
    expect(
      within(assistantMessage as HTMLElement).getByText(
        /mlx-community\/Llama-3.2-3B-Instruct-4bit/i,
      ),
    ).toBeInTheDocument();
    expect(
      within(assistantMessage as HTMLElement).getByText(/adapter capability groundedness/i),
    ).toBeInTheDocument();
    expect(within(assistantMessage as HTMLElement).getAllByText(/groundedness/i)).toHaveLength(2);
    expect(within(assistantMessage as HTMLElement).getByText(/eval passed/i)).toBeInTheDocument();
    expect(
      within(assistantMessage as HTMLElement).getByText(/deterministic:exact_match/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view trace/i })).toHaveAttribute(
      'href',
      '/traces?project_id=default',
    );
  });

  it('shows errors without adding a synthetic assistant response', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.chatCompletion).mockRejectedValue(new Error('gateway 502: upstream down'));

    render(<ChatClient capabilities={capabilities} loadError={null} />);

    await user.type(screen.getByLabelText(/message/i), 'Hello');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByText(/gateway 502: upstream down/i)).toBeInTheDocument();
    expect(screen.queryByText(/assistant/i)).not.toBeInTheDocument();
  });

  it('permits chat without capabilities and shows creation guidance', async () => {
    const user = userEvent.setup();
    vi.mocked(gateway.chatCompletion).mockResolvedValue({
      response: {
        id: 'chatcmpl_2',
        object: 'chat.completion',
        created: 1,
        model: 'llama3.2:3b',
        choices: [
          {
            index: 0,
            finish_reason: 'stop',
            message: { role: 'assistant', content: 'Capabilities can be added later.' },
          },
        ],
      },
      traceId: 'trace_empty',
    });

    render(<ChatClient capabilities={[]} loadError={null} />);

    expect(screen.getByText(/eval and failure workflows need a capability/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /create one/i })).toHaveAttribute(
      'href',
      '/capabilities/new',
    );

    await user.type(screen.getByLabelText(/message/i), 'Can I still chat?');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(gateway.chatCompletion).toHaveBeenCalledWith({
      model: 'local-ollama:llama3.2:3b',
      messages: [{ role: 'user', content: 'Can I still chat?' }],
      projectId: 'default',
      capabilityIds: [],
      tags: 'source=dashboard-chat',
    });
    expect(await screen.findByText('Capabilities can be added later.')).toBeInTheDocument();
  });
});
