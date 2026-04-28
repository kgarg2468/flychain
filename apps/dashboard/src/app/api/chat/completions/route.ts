import { NextRequest, NextResponse } from 'next/server';

const GATEWAY_URL = process.env.FLYCHAIN_GATEWAY_URL ?? 'http://localhost:8080';

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await req.text();
  const headers: Record<string, string> = {
    'content-type': req.headers.get('content-type') ?? 'application/json',
  };

  for (const name of ['x-flychain-project', 'x-flychain-capabilities', 'x-flychain-tags']) {
    const value = req.headers.get(name);
    if (value) headers[name] = value;
  }

  const res = await fetch(`${GATEWAY_URL}/v1/chat/completions`, {
    method: 'POST',
    headers,
    body,
    cache: 'no-store',
  });
  const text = await res.text();
  const responseHeaders: Record<string, string> = {
    'content-type': res.headers.get('content-type') ?? 'application/json',
  };
  const traceId = res.headers.get('x-flychain-trace-id');
  if (traceId) responseHeaders['x-flychain-trace-id'] = traceId;

  return new NextResponse(text, {
    status: res.status,
    headers: responseHeaders,
  });
}
