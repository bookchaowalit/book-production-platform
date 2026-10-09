import { NextRequest, NextResponse } from 'next/server';

import { guardRequest } from '@/lib/request-guard';
import { allowedGet, allowedPost } from '@/lib/routes';

const APP_PORT = 3500;

const TIMEOUT_MS = { get: 10_000, post: 30 * 60_000 };

function hubOrigin(): string | NextResponse {
  const configured = process.env.PRODUCTION_HUB_API_ORIGIN || 'http://127.0.0.1:3460';
  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    return NextResponse.json({ error: { code: 'origin_invalid', message: 'ตั้งค่า PRODUCTION_HUB_API_ORIGIN ไม่ถูกต้อง' } }, { status: 503 });
  }
  const loopback = url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(url.hostname);
  if (!loopback) {
    return NextResponse.json({ error: { code: 'origin_not_allowed', message: 'Production Hub API ต้องอยู่ในเครื่องเดียวกัน (loopback) เท่านั้น' } }, { status: 503 });
  }
  return url.toString().replace(/\/$/, '');
}

function refused(request: NextRequest): NextResponse | null {
  const guard = guardRequest(request, process.env, APP_PORT);
  if (guard.ok) return null;
  return NextResponse.json({ error: { code: guard.code, message: guard.message } }, { status: guard.status });
}

const unavailable = () => NextResponse.json(
  { error: { code: 'hub_unavailable', message: 'ติดต่อ Production Hub ไม่ได้ — เปิดด้วย `python -m production_hub.api` แล้วลองใหม่' } },
  { status: 503 },
);

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const blocked = refused(request);
  if (blocked) return blocked;
  const route = (await context.params).path.join('/');
  if (!allowedGet(route)) return NextResponse.json({ error: { code: 'not_found', message: 'ไม่พบเส้นทางนี้' } }, { status: 404 });
  const origin = hubOrigin();
  if (typeof origin !== 'string') return origin;
  try {
    const response = await fetch(`${origin}/api/${route}${request.nextUrl.search}`, {
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(TIMEOUT_MS.get),
    });
    const contentType = response.headers.get('content-type') ?? 'application/octet-stream';
    const headers: Record<string, string> = {
      'Content-Type': contentType, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    };
    if (!contentType.startsWith('application/json')) {
      headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'";
    }
    return new NextResponse(response.body, { status: response.status, headers });
  } catch {
    return unavailable();
  }
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const blocked = refused(request);
  if (blocked) return blocked;
  const route = (await context.params).path.join('/');
  if (!allowedPost(route)) return NextResponse.json({ error: { code: 'not_found', message: 'ไม่พบเส้นทางนี้' } }, { status: 404 });
  const origin = hubOrigin();
  if (typeof origin !== 'string') return origin;
  const headers = new Headers({ 'Content-Type': 'application/json', 'X-Hub-Client': 'console' });
  const token = process.env.PRODUCTION_HUB_API_TOKEN;
  if (token) headers.set('Authorization', `Bearer ${token}`);
  try {
    const response = await fetch(`${origin}/api/${route}`, {
      method: 'POST', headers, body: await request.text(), cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS.post),
    });
    return new NextResponse(response.body, {
      status: response.status,
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  } catch {
    return unavailable();
  }
}
