const { API_ORIGIN, PROJECT_NAME, SERVICE_NAMES, WEB_ORIGIN } = require("../../src/runtime/constants");

/** @typedef {import("../../src/runtime/types").ServiceName} ServiceName */
/** @typedef {import("../../src/runtime/types").ServiceState} ServiceState */
/** @typedef {{ api: "healthy" | "unavailable" | "foreign", web: "healthy" | "unavailable" | "foreign" }} ProbeResult */
/** @typedef {{ exitCode: number, stdout: string, stderr: string }} ComposeResult */
/** @typedef {{
 * snapshot?: Partial<Record<ServiceName, ServiceState>>,
 * snapshotResults?: Array<Partial<Record<ServiceName, ServiceState>>>,
 * probeResults?: ProbeResult[],
 * preflightResult?: { composeVersion: string, engine: "linux" } | Error,
 * composeResults?: Array<ComposeResult | Error>
 * }} FakeRuntimeOptions
 */

/** @param {Partial<Record<ServiceName, ServiceState>>} [snapshot] */
function copySnapshot(snapshot = {}) {
  return /** @type {Record<ServiceName, ServiceState>} */ (
    Object.fromEntries(SERVICE_NAMES.map((service) => [
      service,
      snapshot[/** @type {ServiceName} */ (service)] ?? "unknown",
    ]))
  );
}

/** @param {import("../../src/runtime/types").RuntimeStatus} status */
function copyStatus(status) {
  return { ...status, services: { ...status.services } };
}

/** @param {FakeRuntimeOptions} [options] */
function createFakeRuntime({
  snapshot,
  snapshotResults,
  probeResults = [{ api: "unavailable", web: "unavailable" }],
  preflightResult = { composeVersion: "2.30.3", engine: "linux" },
  composeResults = [{ exitCode: 0, stdout: "", stderr: "" }],
} = {}) {
  const snapshots = snapshotResults?.length ? snapshotResults : [snapshot ?? {}];
  /** @type {ProbeResult[]} */
  const probes = probeResults.length ? probeResults : [{ api: "unavailable", web: "unavailable" }];
  const compose = composeResults.length ? composeResults : [{ exitCode: 0, stdout: "", stderr: "" }];
  /** @type {string[][]} */
  const composeWrites = [];
  /** @type {Array<import("../../src/runtime/types").RuntimeStatus>} */
  const publishedStatuses = [];
  let clockMs = 0;
  let snapshotIndex = 0;
  let probeIndex = 0;
  let composeIndex = 0;
  let preflightCalls = 0;
  let inspectCalls = 0;
  let probeCalls = 0;

  const dependencies = {
    dockerCli: {
      preflight: async () => {
        preflightCalls += 1;
        if (preflightResult instanceof Error) throw preflightResult;
        return preflightResult;
      },
      run: async (/** @type {string[]} */ args, /** @type {{ timeoutMs?: number }} */ _options = {}) => {
        composeWrites.push([...args]);
        const result = compose[Math.min(composeIndex, compose.length - 1)] ?? { exitCode: 0, stdout: "", stderr: "" };
        composeIndex += 1;
        if (result instanceof Error) throw result;
        return { ...result };
      },
    },
    paths: {
      bundleRoot: "C:\\repo",
      composeFile: "C:\\repo\\services\\video-postproduction\\docker-compose.yml",
      composeProjectDirectory: "C:\\repo\\services\\video-postproduction",
    },
    projectName: PROJECT_NAME,
    apiOrigin: API_ORIGIN,
    webOrigin: WEB_ORIGIN,
    fetchImpl: async () => { throw new Error("unexpected live fetch in fake runtime"); },
    inspectComposeProject: async () => {
      inspectCalls += 1;
      const result = snapshots[Math.min(snapshotIndex, snapshots.length - 1)] ?? {};
      snapshotIndex += 1;
      return copySnapshot(result);
    },
    probeRuntime: async () => {
      probeCalls += 1;
      const result = probes[Math.min(probeIndex, probes.length - 1)] ?? { api: "unavailable", web: "unavailable" };
      probeIndex += 1;
      return { ...result };
    },
    sleep: async (/** @type {number} */ durationMs) => { clockMs += durationMs; },
    now: () => clockMs,
    publishStatus: async (/** @type {import("../../src/runtime/types").RuntimeStatus} */ status) => {
      publishedStatuses.push(copyStatus(status));
    },
  };

  return {
    dependencies,
    composeWrites,
    publishedStatuses,
    get preflightCalls() { return preflightCalls; },
    get inspectCalls() { return inspectCalls; },
    get probeCalls() { return probeCalls; },
    get now() { return clockMs; },
  };
}

module.exports = { createFakeRuntime };
