import { afterEach, describe, expect, it, vi } from 'vitest';

function headersRecord(headers: HeadersInit | undefined): Record<string, string> {
  return Object.fromEntries(new Headers(headers).entries());
}

describe('POST /api/chat/completions', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('forwards the body and FlyChain headers to the configured gateway', async () => {
    vi.stubEnv('FLYCHAIN_GATEWAY_URL', 'http://gateway.test');
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
            'x-flychain-trace-id': 'trace_route_1',
          },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { POST } = await import('./route');

    const response = await POST(
      new Request('http://dashboard.test/api/chat/completions', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-flychain-project': 'demo',
          'x-flychain-capabilities': 'groundedness',
          'x-flychain-tags': 'source=dashboard-chat',
        },
        body: JSON.stringify({
          model: 'local-ollama:llama3.2:3b',
          messages: [{ role: 'user', content: 'ping' }],
          stream: false,
        }),
      }) as never,
    );

    expect(fetchMock).toHaveBeenCalledWith('http://gateway.test/v1/chat/completions', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-flychain-project': 'demo',
        'x-flychain-capabilities': 'groundedness',
        'x-flychain-tags': 'source=dashboard-chat',
      },
      body: JSON.stringify({
        model: 'local-ollama:llama3.2:3b',
        messages: [{ role: 'user', content: 'ping' }],
        stream: false,
      }),
      cache: 'no-store',
    });
    expect(response.status).toBe(200);
    expect(response.headers.get('x-flychain-trace-id')).toBe('trace_route_1');
    expect(headersRecord(response.headers)['content-type']).toContain('application/json');
    await expect(response.json()).resolves.toMatchObject({
      id: 'chatcmpl_1',
      choices: [{ message: { content: 'pong' } }],
    });
  });
});
