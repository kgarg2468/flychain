import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SettingsClient } from './client';
import { gateway } from '@/lib/gateway';

vi.mock('@/lib/gateway', async () => {
  const actual = await vi.importActual<typeof import('@/lib/gateway')>('@/lib/gateway');
  return {
    ...actual,
    gateway: {
      ...actual.gateway,
      updateSettings: vi.fn().mockResolvedValue({
        settings: {
          judge_model: 'llama3.2:3b',
          embedding_model: 'nomic-embed-text',
          min_cluster_size: 3,
          auto_eval_new_traces: true,
          auto_cluster_failures: false,
        },
        openai_configured: false,
        anthropic_configured: false,
      }),
    },
  };
});

describe('SettingsClient', () => {
  it('saves edited runtime knobs', async () => {
    const user = userEvent.setup();
    render(
      <SettingsClient
        initial={{
          settings: {
            judge_model: 'llama3.2:1b',
            embedding_model: 'nomic-embed-text',
            min_cluster_size: 2,
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
        }}
      />,
    );

    await user.clear(screen.getByLabelText(/judge model/i));
    await user.type(screen.getByLabelText(/judge model/i), 'llama3.2:3b');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(gateway.updateSettings).toHaveBeenCalledWith({
      judge_model: 'llama3.2:3b',
      embedding_model: 'nomic-embed-text',
      min_cluster_size: 2,
      auto_eval_new_traces: false,
      auto_cluster_failures: false,
    });
    expect(await screen.findByText(/saved to local runtime settings/i)).toBeInTheDocument();
  });
});
