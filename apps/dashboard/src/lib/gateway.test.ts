import { afterEach, describe, expect, it, vi } from 'vitest';

import { gateway } from './gateway';

describe('gateway.chatCompletion', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the same-origin API route and returns trace plus adapter proof headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'chatcmpl_1',
          choices: [{ message: { role: 'assistant', content: 'pong' } }],
        }),
        {
          status: 200,
          headers: {
            'content-type': 'application/json',
            'x-flychain-trace-id': 'trace_helper_1',
            'x-flychain-active-adapter-run-id': 'run_mlx',
            'x-flychain-active-adapter-capability-id': 'groundedness',
            'x-flychain-provider': 'local-mlx',
            'x-flychain-model': 'mlx-community/Llama-3.2-3B-Instruct-4bit',
          },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await gateway.chatCompletion({
      model: 'local-ollama:llama3.2:3b',
      messages: [{ role: 'user', content: 'ping' }],
      projectId: 'default',
      capabilityIds: ['groundedness'],
      tags: 'source=dashboard-chat',
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/chat/completions', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-flychain-project': 'default',
        'x-flychain-capabilities': 'groundedness',
        'x-flychain-tags': 'source=dashboard-chat',
      },
      body: JSON.stringify({
        model: 'local-ollama:llama3.2:3b',
        messages: [{ role: 'user', content: 'ping' }],
        stream: false,
      }),
    });
    expect(result.traceId).toBe('trace_helper_1');
    expect(result.activeAdapter).toEqual({
      runId: 'run_mlx',
      capabilityId: 'groundedness',
      provider: 'local-mlx',
      model: 'mlx-community/Llama-3.2-3B-Instruct-4bit',
    });
    expect(result.response.choices[0]?.message.content).toBe('pong');
  });
});

describe('gateway.updateSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the same-origin gateway proxy from the browser', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          settings: {
            judge_model: 'llama3.2:3b',
            embedding_model: 'nomic-embed-text',
            min_cluster_size: 4,
            auto_eval_new_traces: true,
            auto_cluster_failures: true,
          },
          openai_configured: false,
          anthropic_configured: false,
        }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await gateway.updateSettings({
      min_cluster_size: 4,
      auto_eval_new_traces: true,
      auto_cluster_failures: true,
    });

    expect(fetchMock).toHaveBeenCalledWith('/api/gateway/v1/settings', {
      cache: 'no-store',
      method: 'PUT',
      headers: {
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        min_cluster_size: 4,
        auto_eval_new_traces: true,
        auto_cluster_failures: true,
      }),
    });
  });
});
