import { NextRequest, NextResponse } from 'next/server';

const GATEWAY_URL = process.env.FLYCHAIN_GATEWAY_URL ?? 'http://localhost:8080';

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await req.text();
  const res = await fetch(`${GATEWAY_URL}/v1/capabilities/compiler/compile`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body,
    cache: 'no-store',
  });
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
  });
}
