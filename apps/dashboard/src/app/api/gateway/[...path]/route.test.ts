import { afterEach, describe, expect, it, vi } from 'vitest';

function headersRecord(headers: HeadersInit | undefined): Record<string, string> {
  return Object.fromEntries(new Headers(headers).entries());
}

describe('/api/gateway proxy', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('forwards PUT settings requests to the configured gateway', async () => {
    vi.stubEnv('FLYCHAIN_GATEWAY_URL', 'http://gateway.test');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ settings: { min_cluster_size: 4 } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { PUT } = await import('./route');

    const response = await PUT(
      new Request('http://dashboard.test/api/gateway/v1/settings?dry_run=0', {
        method: 'PUT',
        headers: {
          'content-type': 'application/json',
          'x-flychain-project': 'demo',
        },
        body: JSON.stringify({ min_cluster_size: 4 }),
      }) as never,
      { params: { path: ['v1', 'settings'] } },
    );

    expect(fetchMock).toHaveBeenCalledWith('http://gateway.test/v1/settings?dry_run=0', {
      method: 'PUT',
      headers: {
        'content-type': 'application/json',
        'x-flychain-project': 'demo',
      },
      body: JSON.stringify({ min_cluster_size: 4 }),
      cache: 'no-store',
    });
    expect(response.status).toBe(200);
    expect(headersRecord(response.headers)['content-type']).toContain('application/json');
    await expect(response.json()).resolves.toMatchObject({
      settings: { min_cluster_size: 4 },
    });
  });

  it('preserves gateway error status and text', async () => {
    vi.stubEnv('FLYCHAIN_GATEWAY_URL', 'http://gateway.test');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('bad settings', {
          status: 422,
          headers: { 'content-type': 'text/plain' },
        }),
      ),
    );
    const { PUT } = await import('./route');

    const response = await PUT(
      new Request('http://dashboard.test/api/gateway/v1/settings', {
        method: 'PUT',
        body: '{}',
      }) as never,
      { params: { path: ['v1', 'settings'] } },
    );

    expect(response.status).toBe(422);
    await expect(response.text()).resolves.toBe('bad settings');
  });
});
