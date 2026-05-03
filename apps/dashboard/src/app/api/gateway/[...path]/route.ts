import { NextRequest, NextResponse } from 'next/server';

const GATEWAY_URL = process.env.FLYCHAIN_GATEWAY_URL ?? 'http://localhost:8080';
const FORWARDED_HEADERS = [
  'content-type',
  'x-flychain-project',
  'x-flychain-capabilities',
  'x-flychain-tags',
  'x-flychain-candidate-run-id',
];
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

interface Context {
  params: { path: string[] };
}

async function proxy(req: NextRequest, context: Context): Promise<NextResponse> {
  const sourceUrl = new URL(req.url);
  const path = context.params.path.map(encodeURIComponent).join('/');
  const targetUrl = `${GATEWAY_URL}/${path}${sourceUrl.search}`;
  const headers: Record<string, string> = {};

  for (const name of FORWARDED_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers[name] = value;
  }

  const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await req.text();
  const res = await fetch(targetUrl, {
    method: req.method,
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

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
