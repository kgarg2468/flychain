import { afterEach, describe, expect, it, vi } from 'vitest';

import { gateway } from './gateway';

describe('gateway.chatCompletion', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the same-origin API route and returns the trace id header', async () => {
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
    expect(result.response.choices[0]?.message.content).toBe('pong');
  });
});
