const { WEB_ORIGIN } = require("../runtime/constants");

const STUDIO_CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: http://127.0.0.1:8000 http://localhost:8000",
  "media-src 'self' blob: http://127.0.0.1:8000 http://localhost:8000",
  "font-src 'self' data:",
  "connect-src 'self' http://127.0.0.1:8000 http://localhost:8000",
  "object-src 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

/**
 * Add the desktop renderer's enforcing policy only to the exact local Studio origin.
 * Keeping a server-provided CSP as a separate header value preserves its stricter rules.
 *
 * @param {string} url
 * @param {Record<string, string[] | string>} [responseHeaders]
 * @returns {Record<string, string[] | string>}
 */
function withStudioContentSecurityPolicy(url, responseHeaders = {}) {
  let origin;
  try {
    origin = new URL(url).origin;
  } catch {
    return responseHeaders;
  }
  if (origin !== WEB_ORIGIN) return responseHeaders;

  const nextHeaders = { ...responseHeaders };
  const policyHeaderName = Object.keys(nextHeaders).find(
    (headerName) => headerName.toLowerCase() === "content-security-policy",
  ) ?? "Content-Security-Policy";
  const existing = nextHeaders[policyHeaderName];
  const values = existing === undefined
    ? []
    : Array.isArray(existing) ? [...existing] : [existing];
  values.push(STUDIO_CONTENT_SECURITY_POLICY);
  nextHeaders[policyHeaderName] = values;
  return nextHeaders;
}

module.exports = {
  STUDIO_CONTENT_SECURITY_POLICY,
  withStudioContentSecurityPolicy,
};
