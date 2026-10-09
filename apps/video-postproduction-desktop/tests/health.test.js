const assert = require("node:assert/strict");
const test = require("node:test");
const { API_ORIGIN, WEB_ORIGIN } = require("../src/runtime/constants");
const { probeRuntime } = require("../src/runtime/health");

/** @param {string} service @param {string} status @param {number} [httpStatus] */
function apiResponse(service = "video-postproduction-api", status = "ok", httpStatus = 200) {
  return new Response(JSON.stringify({ service, status }), {
    status: httpStatus,
    headers: { "content-type": "application/json" },
  });
}

/** @param {string} title @param {number} [httpStatus] */
function webResponse(title = "Post-production studio", httpStatus = 200) {
  return new Response(`<html><head><title>${title}</title></head><body></body></html>`, {
    status: httpStatus,
    headers: { "content-type": "text/html" },
  });
}

/** @param {Response} api @param {Response} web */
function responseFetch(api, web) {
  /** @type {typeof fetch} */
  const fetchImpl = async (input) => String(input).endsWith("/healthz") ? api : web;
  return fetchImpl;
}

test("probes the fixed API health route and web origin for their exact identities", async () => {
  /** @type {string[]} */
  const requestedUrls = [];
  /** @type {typeof fetch} */
  const fetchImpl = async (input) => {
    const url = String(input);
    requestedUrls.push(url);
    return url.endsWith("/healthz") ? apiResponse() : webResponse();
  };

  const result = await probeRuntime({ fetchImpl, apiOrigin: API_ORIGIN, webOrigin: WEB_ORIGIN, timeoutMs: 500 });

  assert.deepEqual(result, { api: "healthy", web: "healthy" });
  assert.deepEqual(requestedUrls, [`${API_ORIGIN}/healthz`, `${WEB_ORIGIN}/`]);
});

test("test_wrong_api_identity_is_foreign", async () => {
  const result = await probeRuntime({
    fetchImpl: responseFetch(apiResponse("unrelated-api"), webResponse()),
    apiOrigin: "http://127.0.0.1:8000",
    webOrigin: "http://127.0.0.1:3400",
    timeoutMs: 500,
  });

  assert.equal(result.api, "foreign");
});

test("test_wrong_web_title_is_foreign", async () => {
  const result = await probeRuntime({
    fetchImpl: responseFetch(apiResponse(), webResponse("Another application")),
    apiOrigin: "http://127.0.0.1:8000",
    webOrigin: "http://127.0.0.1:3400",
    timeoutMs: 500,
  });

  assert.equal(result.web, "foreign");
});

test("does not accept a page title that only contains the expected title", async () => {
  const result = await probeRuntime({
    fetchImpl: responseFetch(apiResponse(), webResponse("Post-production studio Preview")),
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 500,
  });

  assert.equal(result.web, "foreign");
});

test("classifies malformed API JSON as a foreign response", async () => {
  const fetchImpl = responseFetch(new Response("not-json", { status: 200 }), webResponse());

  const result = await probeRuntime({
    fetchImpl,
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 500,
  });

  assert.equal(result.api, "foreign");
});

test("classifies a reachable matching API that reports a non-success status as unavailable", async () => {
  const result = await probeRuntime({
    fetchImpl: responseFetch(apiResponse("video-postproduction-api", "ok", 503), webResponse()),
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 500,
  });

  assert.equal(result.api, "unavailable");
});

test("classifies a matching web page with a non-success status as unavailable", async () => {
  const result = await probeRuntime({
    fetchImpl: responseFetch(apiResponse(), webResponse("Post-production studio", 503)),
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 500,
  });

  assert.equal(result.web, "unavailable");
});

test("classifies connection failures as unavailable without leaking error details", async () => {
  /** @type {typeof fetch} */
  const fetchImpl = async () => { throw new Error("private connection detail"); };

  const result = await probeRuntime({
    fetchImpl,
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 500,
  });

  assert.deepEqual(result, { api: "unavailable", web: "unavailable" });
});

test("never falls back to the global fetch when no implementation is injected", async () => {
  const originalFetch = globalThis.fetch;
  let globalFetchCalls = 0;
  /** @type {typeof fetch} */
  const globalFetchFake = async () => {
    globalFetchCalls += 1;
    return apiResponse();
  };
  globalThis.fetch = globalFetchFake;

  try {
    await assert.rejects(
      // @ts-expect-error this case deliberately omits the required injection
      probeRuntime({ apiOrigin: API_ORIGIN, webOrigin: WEB_ORIGIN, timeoutMs: 500 }),
      /ต้องระบุ fetchImpl/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(globalFetchCalls, 0);
});

/** @param {AbortSignal} signal @returns {Promise<Response>} */
function waitForAbort(signal) {
  return new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });
}

test("gives each request its own abort controller and timeout", async () => {
  /** @type {Map<string, AbortSignal>} */
  const signals = new Map();
  /** @type {typeof fetch} */
  const fetchImpl = async (input, init) => {
    const url = String(input);
    const signal = init?.signal;
    if (!(signal instanceof AbortSignal)) throw new Error("missing abort signal");
    signals.set(url, signal);

    if (url.endsWith("/healthz")) return waitForAbort(signal);
    return webResponse();
  };

  const result = await probeRuntime({
    fetchImpl,
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    timeoutMs: 20,
  });

  const apiSignal = signals.get(`${API_ORIGIN}/healthz`);
  const webSignal = signals.get(`${WEB_ORIGIN}/`);
  assert.deepEqual(result, { api: "unavailable", web: "healthy" });
  assert.ok(apiSignal);
  assert.ok(webSignal);
  assert.notEqual(apiSignal, webSignal);
  assert.equal(apiSignal.aborted, true);
  assert.equal(webSignal.aborted, false);
});
