import { NextRequest, NextResponse } from 'next/server';

const GATEWAY_URL = process.env.FLYCHAIN_GATEWAY_URL ?? 'http://localhost:8080';
const RESPONSE_HEADERS = [
  'x-flychain-trace-id',
  'x-flychain-adapter-run-id',
  'x-flychain-adapter-capability-id',
  'x-flychain-adapter-routing-mode',
  'x-flychain-active-adapter-run-id',
  'x-flychain-active-adapter-capability-id',
  'x-flychain-provider',
  'x-flychain-model',
];

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await req.text();
  const headers: Record<string, string> = {
    'content-type': req.headers.get('content-type') ?? 'application/json',
  };

  for (const name of [
    'x-flychain-project',
    'x-flychain-capabilities',
    'x-flychain-tags',
    'x-flychain-candidate-run-id',
  ]) {
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
  for (const name of RESPONSE_HEADERS) {
    const value = res.headers.get(name);
    if (value) responseHeaders[name] = value;
  }

  return new NextResponse(text, {
    status: res.status,
    headers: responseHeaders,
  });
}
