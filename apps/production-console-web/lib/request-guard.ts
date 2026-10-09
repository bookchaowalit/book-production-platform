/**
 * Confused-deputy guard for the loopback API proxy.
 *
 * The proxy attaches server-side credentials (for example the Production Hub
 * bearer token) that the browser never sees, so it must refuse requests that a
 * different site could trigger:
 *
 * - `Host` must be a loopback name on this app's port, which defeats DNS
 *   rebinding (a rebound page still sends its own hostname).
 * - State-changing methods must come from this origin: `Origin` must equal
 *   `http://<Host>`, or, when a browser omits `Origin`, `Sec-Fetch-Site` must be
 *   `same-origin`. Mirrors `sameOrigin()` in `apps/crm-web/lib/session.ts`.
 * - State-changing methods must send `Content-Type: application/json`, which a
 *   cross-site HTML form or "simple" fetch cannot send without a CORS preflight.
 */

const LOOPBACK_HOSTS = ['127.0.0.1', 'localhost', '[::1]'];
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export type GuardResult = { ok: true } | { ok: false; status: 403 | 415; code: string; message: string };

interface GuardedRequest {
  method: string;
  headers: Headers;
}

/** `Host` values this app answers to: loopback names on the port Next.js listens on. */
export function allowedHosts(env: Record<string, string | undefined>, defaultPort: number): Set<string> {
  const port = env.PORT && /^\d{1,5}$/.test(env.PORT) ? env.PORT : String(defaultPort);
  return new Set(LOOPBACK_HOSTS.map((host) => `${host}:${port}`));
}

export function sameOrigin(origin: string | null, fetchSite: string | null, host: string): boolean {
  if (origin !== null) return origin === `http://${host}`;
  return fetchSite === 'same-origin';
}

function jsonContentType(value: string | null): boolean {
  return (value ?? '').split(';')[0].trim().toLowerCase() === 'application/json';
}

export function guardRequest(
  request: GuardedRequest, env: Record<string, string | undefined>, defaultPort: number,
): GuardResult {
  const host = (request.headers.get('host') ?? '').toLowerCase();
  if (!allowedHosts(env, defaultPort).has(host)) {
    return { ok: false, status: 403, code: 'host_not_allowed', message: 'เปิดผ่าน 127.0.0.1 หรือ localhost บนพอร์ตของแอปเท่านั้น' };
  }
  if (SAFE_METHODS.has(request.method.toUpperCase())) return { ok: true };
  if (!sameOrigin(request.headers.get('origin'), request.headers.get('sec-fetch-site'), host)) {
    return { ok: false, status: 403, code: 'cross_origin', message: 'ปฏิเสธคำขอที่ไม่ได้มาจากหน้าเว็บนี้' };
  }
  if (!jsonContentType(request.headers.get('content-type'))) {
    return { ok: false, status: 415, code: 'unsupported_media_type', message: 'ต้องส่งข้อมูลเป็น application/json' };
  }
  return { ok: true };
}
