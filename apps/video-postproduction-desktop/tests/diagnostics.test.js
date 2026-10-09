const assert = require("node:assert/strict");
const test = require("node:test");
const { sanitizeDiagnostic } = require("../src/runtime/diagnostics");

test("redacts values for environment names containing PASSWORD, TOKEN, SECRET, or KEY", () => {
  const safe = sanitizeDiagnostic(
    "POSTGRES_PASSWORD=local-secret API_TOKEN=abc123 JWT_SECRET='private value' API_KEY=\"key-value\" PORT=8000",
  );

  for (const secret of ["local-secret", "abc123", "private value", "key-value"]) {
    assert.equal(safe.includes(secret), false);
  }
  assert.equal(safe.includes("PORT=8000"), true);
});

test("normalizes controls and caps diagnostic text", () => {
  const safe = sanitizeDiagnostic(`first\u0000line\n${"x".repeat(5000)}`);

  assert.equal(safe.includes("\u0000"), false);
  assert.equal(safe.length <= 4096, true);
});
