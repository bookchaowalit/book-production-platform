const { API_ORIGIN, WEB_ORIGIN } = require("./constants");

const DEFAULT_TIMEOUT_MS = 2000;
const EXPECTED_API_SERVICE = "video-postproduction-api";
const EXPECTED_WEB_TITLE = "Post-production studio";

/** @typedef {"healthy" | "unavailable" | "foreign"} EndpointState */
/** @typedef {{ ok: boolean, json: () => Promise<unknown>, text: () => Promise<string> }} ProbeResponse */
/** @typedef {(url: string, init: { method: "GET", redirect: "manual", signal: AbortSignal }) => Promise<ProbeResponse>} FetchImpl */
/** @typedef {{ fetchImpl: FetchImpl, apiOrigin?: string, webOrigin?: string, timeoutMs?: number }} ProbeRuntimeOptions */

/** @param {string} origin @returns {string} */
function requireLoopbackOrigin(origin) {
  let parsed;
  try {
    parsed = new URL(origin);
  } catch {
    throw new Error("invalid loopback origin");
  }
  const loopbackHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
  if (parsed.protocol !== "http:" || !loopbackHosts.has(parsed.hostname)) {
    throw new Error("only loopback HTTP origins are allowed");
  }
  return parsed.origin;
}

/**
 * @param {FetchImpl} fetchImpl
 * @param {string} url
 * @param {number} timeoutMs
 * @param {(response: ProbeResponse) => Promise<EndpointState>} verifyResponse
 * @returns {Promise<EndpointState>}
 */
async function probeEndpoint(fetchImpl, url, timeoutMs, verifyResponse) {
  const controller = new AbortController();
  /** @type {NodeJS.Timeout | undefined} */
  let timer;

  const checkResponse = (async () => {
    let response;
    try {
      response = await fetchImpl(url, { method: "GET", redirect: "manual", signal: controller.signal });
    } catch {
      return /** @type {EndpointState} */ ("unavailable");
    }
    let identity;
    try {
      identity = await verifyResponse(response);
    } catch {
      return controller.signal.aborted ? "unavailable" : "foreign";
    }
    if (identity !== "healthy") return identity;
    return response.ok ? "healthy" : "unavailable";
  })();

  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve("unavailable");
    }, timeoutMs);
  });

  try {
    return await Promise.race([checkResponse, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** @param {ProbeResponse} response @returns {Promise<EndpointState>} */
async function verifyApiIdentity(response) {
  const payload = await response.json();
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return "foreign";
  const body = /** @type {Record<string, unknown>} */ (payload);
  return body.service === EXPECTED_API_SERVICE && body.status === "ok" ? "healthy" : "foreign";
}

/** @param {ProbeResponse} response @returns {Promise<EndpointState>} */
async function verifyWebIdentity(response) {
  const html = await response.text();
  const title = html.match(/<title\b[^>]*>([\s\S]*?)<\/title\s*>/i)?.[1]?.trim();
  return title === EXPECTED_WEB_TITLE ? "healthy" : "foreign";
}

/** @param {ProbeRuntimeOptions} options @returns {Promise<{ api: EndpointState, web: EndpointState }>} */
async function probeRuntime({
  fetchImpl,
  apiOrigin = API_ORIGIN,
  webOrigin = WEB_ORIGIN,
  timeoutMs = DEFAULT_TIMEOUT_MS,
}) {
  if (typeof fetchImpl !== "function") throw new Error("ต้องระบุ fetchImpl");
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("invalid probe timeout");

  const apiUrl = new URL("/healthz", requireLoopbackOrigin(apiOrigin)).toString();
  const webUrl = new URL("/", requireLoopbackOrigin(webOrigin)).toString();
  const [api, web] = await Promise.all([
    probeEndpoint(fetchImpl, apiUrl, timeoutMs, verifyApiIdentity),
    probeEndpoint(fetchImpl, webUrl, timeoutMs, verifyWebIdentity),
  ]);
  return { api, web };
}

module.exports = { probeRuntime };
