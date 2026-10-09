const assert = require("node:assert/strict");
const test = require("node:test");

const {
  STUDIO_CONTENT_SECURITY_POLICY,
  withStudioContentSecurityPolicy,
} = require("../src/security/studio-csp");

test("studio CSP allows only the local studio and API surfaces without unsafe-eval", () => {
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /default-src 'self'/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /script-src 'self' 'unsafe-inline'/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /style-src 'self' 'unsafe-inline'/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /http:\/\/127\.0\.0\.1:8000/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /http:\/\/localhost:8000/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /object-src 'none'/);
  assert.match(STUDIO_CONTENT_SECURITY_POLICY, /frame-src 'none'/);
  assert.doesNotMatch(STUDIO_CONTENT_SECURITY_POLICY, /unsafe-eval|https:\/\/|\*/);
});

test("studio CSP is appended while preserving server policy headers", () => {
  const responseHeaders = {
    "Content-Security-Policy": ["default-src 'none'; script-src 'self'"],
    "X-Content-Type-Options": ["nosniff"],
  };

  const result = withStudioContentSecurityPolicy(
    "http://127.0.0.1:3400/projects/one",
    responseHeaders,
  );

  assert.notEqual(result, responseHeaders);
  assert.deepEqual(result["Content-Security-Policy"], [
    "default-src 'none'; script-src 'self'",
    STUDIO_CONTENT_SECURITY_POLICY,
  ]);
  assert.deepEqual(result["X-Content-Type-Options"], ["nosniff"]);
  assert.deepEqual(responseHeaders["Content-Security-Policy"], ["default-src 'none'; script-src 'self'"]);
});

test("studio CSP is not injected into lookalike origins or invalid URLs", () => {
  const headers = { "Content-Type": ["text/html"] };
  const urls = [
    "http://localhost:3400/projects",
    "http://127.0.0.1:3401/projects",
    "https://127.0.0.1:3400/projects",
    "http://127.0.0.1.evil.example:3400/projects",
    "not a URL",
  ];

  for (const url of urls) {
    assert.deepEqual(withStudioContentSecurityPolicy(url, headers), headers, url);
  }
});

test("studio CSP is created when no server policy exists", () => {
  const result = withStudioContentSecurityPolicy("http://127.0.0.1:3400/", {});
  assert.deepEqual(result["Content-Security-Policy"], [STUDIO_CONTENT_SECURITY_POLICY]);
});
