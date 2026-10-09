import { describe, expect, it } from 'vitest';

import { api, formatBytes, HubError, NEXT_REVIEW, shortId } from './api';
import { allowedGet, allowedPost } from './routes';

const ID = `sha256:${'a'.repeat(64)}`;

const respond = (status: number, body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;

describe('routes allowlist', () => {
  it('accepts only known hub routes', () => {
    expect(allowedGet('v1/artifacts')).toBe(true);
    expect(allowedGet(`v1/artifacts/${ID}/content`)).toBe(true);
    expect(allowedGet('v1/projects/pod-1')).toBe(true);
    expect(allowedGet('v1/artifacts/../../etc')).toBe(false);
    expect(allowedGet(`v1/artifacts/${ID}/delete`)).toBe(false);
    expect(allowedPost('v1/projects/pod-1/package')).toBe(true);
    expect(allowedPost('v1/projects/Pod 1/package')).toBe(false);
    expect(allowedPost('v1/artifacts')).toBe(false);
  });
});

describe('api client', () => {
  it('surfaces hub error messages', async () => {
    const fetcher = respond(422, { error: { code: 'workspace_error', message: 'ไม่มีไฟล์ที่อนุมัติ' } });
    await expect(api.packageProject('pod-1', fetcher)).rejects.toMatchObject({
      code: 'workspace_error', message: 'ไม่มีไฟล์ที่อนุมัติ', status: 422,
    });
  });

  it('reports network and invalid responses in Thai', async () => {
    const offline = (async () => { throw new TypeError('offline'); }) as typeof fetch;
    await expect(api.health(offline)).rejects.toBeInstanceOf(HubError);
    const html = (async () => new Response('<html>', { status: 502 })) as typeof fetch;
    await expect(api.health(html)).rejects.toMatchObject({ code: 'invalid_response' });
  });

  it('builds artifact filters into the query string', async () => {
    let seen = '';
    const fetcher = (async (url: string) => {
      seen = url;
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }) as unknown as typeof fetch;
    await api.artifacts({ kind: 'chart.svg', tool: '', project: 'rep-1' }, fetcher);
    expect(seen).toBe('/api/hub/v1/artifacts?kind=chart.svg&project=rep-1');
  });
});

describe('helpers', () => {
  it('formats sizes and ids', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(15 * 1024 * 1024)).toBe('15 MB');
    expect(shortId(ID)).toBe('aaaaaaaaaaaa');
  });

  it('mirrors hub review transitions', () => {
    expect(NEXT_REVIEW.delivered).toEqual([]);
    expect(NEXT_REVIEW.approved).toContain('delivered');
  });
});
