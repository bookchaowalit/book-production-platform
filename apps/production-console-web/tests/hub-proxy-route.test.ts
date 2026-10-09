import { NextRequest } from 'next/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GET, POST } from '../app/api/hub/[...path]/route';

const ROUTE = 'v1/projects/pod-1/package';
const HOST = '127.0.0.1:3500';

type Upstream = { url: string; init: RequestInit };
let upstream: Upstream[] = [];

function request(method: string, headers: Record<string, string>, body?: string) {
  return new NextRequest(`http://${HOST}/api/hub/${ROUTE}`, { method, headers, body });
}

const context = (route = ROUTE) => ({ params: Promise.resolve({ path: route.split('/') }) });

beforeEach(() => {
  upstream = [];
  vi.stubEnv('PORT', '');
  vi.stubEnv('PRODUCTION_HUB_API_ORIGIN', 'http://127.0.0.1:3460');
  vi.stubEnv('PRODUCTION_HUB_API_TOKEN', 'test-token');
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    upstream.push({ url, init });
    return new Response('{"ok":true}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('hub proxy POST guard', () => {
  it('refuses a cross-site POST without contacting the hub', async () => {
    const response = await POST(request('POST', {
      host: HOST, origin: 'https://evil.example', 'content-type': 'application/json',
    }, '{}'), context());
    expect(response.status).toBe(403);
    expect(upstream).toHaveLength(0);
  });

  it('refuses a cross-site text/plain POST (simple request, no preflight)', async () => {
    const response = await POST(request('POST', {
      host: HOST, 'sec-fetch-site': 'cross-site', 'content-type': 'text/plain',
    }, '{}'), context());
    expect(response.status).toBe(403);
    expect(upstream).toHaveLength(0);
  });

  it('refuses a same-origin POST that is not JSON', async () => {
    const response = await POST(request('POST', {
      host: HOST, origin: `http://${HOST}`, 'content-type': 'text/plain;charset=UTF-8',
    }, '{}'), context());
    expect(response.status).toBe(415);
    expect(upstream).toHaveLength(0);
  });

  it('refuses a DNS-rebinding Host even when Origin matches it', async () => {
    const response = await POST(request('POST', {
      host: 'rebind.example:3500', origin: 'http://rebind.example:3500', 'content-type': 'application/json',
    }, '{}'), context());
    expect(response.status).toBe(403);
    expect(upstream).toHaveLength(0);
  });

  it('refuses a POST with neither Origin nor Sec-Fetch-Site', async () => {
    const response = await POST(request('POST', { host: HOST, 'content-type': 'application/json' }, '{}'), context());
    expect(response.status).toBe(403);
    expect(upstream).toHaveLength(0);
  });

  it('proxies a same-origin JSON POST with the server-side token', async () => {
    const response = await POST(request('POST', {
      host: HOST, origin: `http://${HOST}`, 'content-type': 'application/json',
    }, '{"note":"x"}'), context());
    expect(response.status).toBe(200);
    expect(upstream).toHaveLength(1);
    expect(upstream[0].url).toBe(`http://127.0.0.1:3460/api/${ROUTE}`);
    expect(new Headers(upstream[0].init.headers).get('authorization')).toBe('Bearer test-token');
    expect(upstream[0].init.body).toBe('{"note":"x"}');
  });

  it('accepts Sec-Fetch-Site same-origin when Origin is absent and honours PORT', async () => {
    vi.stubEnv('PORT', '4100');
    const response = await POST(new NextRequest(`http://localhost:4100/api/hub/${ROUTE}`, {
      method: 'POST', body: '{}',
      headers: { host: 'localhost:4100', 'sec-fetch-site': 'same-origin', 'content-type': 'application/json' },
    }), context());
    expect(response.status).toBe(200);
    expect(upstream).toHaveLength(1);
  });
});

describe('hub proxy GET guard', () => {
  it('refuses a rebinding Host on reads', async () => {
    const response = await GET(new NextRequest('http://rebind.example:3500/api/hub/v1/artifacts', {
      headers: { host: 'rebind.example:3500' },
    }), context('v1/artifacts'));
    expect(response.status).toBe(403);
    expect(upstream).toHaveLength(0);
  });

  it('proxies a loopback read', async () => {
    const response = await GET(new NextRequest(`http://${HOST}/api/hub/v1/artifacts`, {
      headers: { host: HOST },
    }), context('v1/artifacts'));
    expect(response.status).toBe(200);
    expect(upstream).toHaveLength(1);
  });
});
