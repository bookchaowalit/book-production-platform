const { composeArgs, inspectComposeProject } = require("./compose");
const { DockerCliError } = require("./docker-cli");
const { probeRuntime } = require("./health");
const { API_ORIGIN, PROJECT_NAME, SERVICE_NAMES, WEB_ORIGIN } = require("./constants");

const FIRST_START_TIMEOUT_MS = 15 * 60 * 1000;
const HEALTH_POLL_INTERVAL_MS = 2000;
const COMMAND_TIMEOUT_MS = 30_000;
const INVALID_COMPOSE_SNAPSHOT_MESSAGES = new Set([
  "invalid Compose status output",
  "unexpected Compose service",
  "duplicate Compose service",
]);

/** @typedef {import("./types").RuntimeStatus} RuntimeStatus */
/** @typedef {import("./types").RuntimeCode} RuntimeCode */
/** @typedef {import("./types").ServiceName} ServiceName */
/** @typedef {import("./types").ServiceState} ServiceState */
/** @typedef {"healthy" | "unavailable" | "foreign"} EndpointState */
/** @typedef {{ api: EndpointState, web: EndpointState }} ProbeResult */
/** @typedef {{ composeFile: string, composeFiles?: string[], composeProjectDirectory: string }} RuntimePaths */
/** @typedef {{ exitCode: number, stdout: string, stderr: string }} DockerResult */
/** @typedef {{
 *   dockerCli: {
 *     preflight: () => Promise<{ composeVersion: string, engine: "linux" }>,
 *     run: (args: string[], options?: { timeoutMs?: number }) => Promise<DockerResult>
 *   },
 *   paths: RuntimePaths,
 *   projectName?: string,
 *   apiOrigin?: string,
 *   webOrigin?: string,
 *   fetchImpl?: typeof fetch,
 *   inspectComposeProject?: (options: { dockerCli: RuntimeManagerOptions["dockerCli"], paths: RuntimePaths, projectName: string, timeoutMs?: number }) => Promise<Record<ServiceName, ServiceState>>,
 *   probeRuntime?: (options: { fetchImpl: typeof fetch, apiOrigin: string, webOrigin: string, timeoutMs?: number }) => Promise<ProbeResult>,
 *   sleep?: (durationMs: number) => Promise<void>,
 *   now?: () => number,
 *   publishStatus?: (status: RuntimeStatus) => void | Promise<void>,
 *   startupTimeoutMs?: number,
 *   pollIntervalMs?: number,
 *   commandTimeoutMs?: number
 * }} RuntimeManagerOptions
 */

/** @returns {Record<ServiceName, ServiceState>} */
function emptyServices() {
  return { postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown" };
}

/** @param {ServiceState} [state] @returns {Record<ServiceName, ServiceState>} */
function uniformServices(state = "unknown") {
  return { postgres: state, redis: state, api: state, worker: state, web: state };
}

/**
 * @param {RuntimeStatus["phase"]} phase
 * @param {RuntimeStatus["action"]} action
 * @param {RuntimeCode} code
 * @param {string} message
 * @param {Record<ServiceName, ServiceState>} services
 * @returns {RuntimeStatus}
 */
function createStatus(phase, action, code, message, services) {
  return { phase, action, code, message, services: { ...services } };
}

/** @param {unknown} value @returns {value is EndpointState} */
function isEndpointState(value) {
  return value === "healthy" || value === "unavailable" || value === "foreign";
}

/** @param {Record<ServiceName, ServiceState>} services @param {ProbeResult} probe @param {boolean} [starting] */
function mergeEndpointStates(services, probe, starting = false) {
  const merged = { ...services };
  for (const service of /** @type {const} */ (["api", "web"])) {
    const endpoint = probe[service];
    if (endpoint === "healthy") {
      merged[/** @type {ServiceName} */ (service)] = "healthy";
    } else if (endpoint === "unavailable") {
      const current = merged[/** @type {ServiceName} */ (service)];
      if (current === "healthy") merged[/** @type {ServiceName} */ (service)] = "unhealthy";
      else if (starting && (current === "unknown" || current === "stopped")) {
        merged[/** @type {ServiceName} */ (service)] = "running";
      }
    }
  }
  return merged;
}

/** @param {Record<ServiceName, ServiceState>} services @param {ServiceState} state */
function allServicesAre(services, state) {
  return SERVICE_NAMES.every((service) => services[/** @type {ServiceName} */ (service)] === state);
}

/** @param {Record<ServiceName, ServiceState>} services */
function noContainers(services) {
  return allServicesAre(services, "unknown");
}

/** @param {unknown} error @returns {boolean} */
function isInvalidComposeSnapshot(error) {
  return error instanceof Error && INVALID_COMPOSE_SNAPSHOT_MESSAGES.has(error.message);
}

/** @param {string} output @returns {boolean} */
function isKnownPortBindFailure(output) {
  const mentionsStudioPort = /\b(?:8000|3400)\b/.test(output);
  const mentionsBindFailure = /\b(?:bind|port is already allocated|ports are not available|address already in use|only one usage of each socket address|listen tcp)\b/i.test(output);
  return mentionsStudioPort && mentionsBindFailure;
}

/** @param {unknown} error @returns {RuntimeCode} */
function codeFromDockerError(error) {
  if (error instanceof DockerCliError && ["missing-docker", "engine-unavailable", "compose-failed"].includes(error.code)) {
    return /** @type {RuntimeCode} */ (error.code);
  }
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = error.code;
    if (code === "missing-docker" || code === "engine-unavailable" || code === "compose-failed") return code;
  }
  return "compose-failed";
}

/** @param {RuntimeCode} code @returns {string} */
function messageForCode(code) {
  if (code === "missing-docker") return "ไม่พบ Docker Desktop หรือ Docker CLI กรุณาติดตั้งก่อนใช้งาน";
  if (code === "engine-unavailable") return "กรุณาเปิด Docker Desktop และเลือกใช้ Linux containers";
  if (code === "port-conflict") return "พอร์ต 8000 หรือ 3400 ถูกใช้งานโดยบริการอื่น แอปจะไม่เปิดบริการนั้น";
  if (code === "partial-runtime") return "พบ runtime ที่ทำงานไม่ครบ ตรวจสอบสถานะก่อนเลือกกู้คืน";
  if (code === "health-timeout") return "รอให้บริการพร้อมนานเกินกำหนด ระบบเดิมยังคงอยู่และไม่ได้ถูกลบ";
  if (code === "invalid-runtime") return "อ่านสถานะบริการไม่สำเร็จ ข้อมูล runtime ไม่ถูกต้อง";
  return "คำสั่ง Docker Compose ไม่สำเร็จ ระบบเดิมยังคงอยู่";
}

/** @param {number} milliseconds @returns {Promise<void>} */
function defaultSleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

class RuntimeManager {
  /** @param {RuntimeManagerOptions} options */
  constructor({
    dockerCli,
    paths,
    projectName = PROJECT_NAME,
    apiOrigin = API_ORIGIN,
    webOrigin = WEB_ORIGIN,
    fetchImpl = globalThis.fetch,
    inspectComposeProject: inspectProject = inspectComposeProject,
    probeRuntime: probe = probeRuntime,
    sleep = defaultSleep,
    now = Date.now,
    publishStatus = async () => {},
    startupTimeoutMs = FIRST_START_TIMEOUT_MS,
    pollIntervalMs = HEALTH_POLL_INTERVAL_MS,
    commandTimeoutMs = COMMAND_TIMEOUT_MS,
  }) {
    this.dockerCli = dockerCli;
    this.paths = paths;
    this.projectName = projectName;
    this.apiOrigin = apiOrigin;
    this.webOrigin = webOrigin;
    this.fetchImpl = fetchImpl;
    this.inspectComposeProject = inspectProject;
    this.probeRuntime = probe;
    this.sleep = sleep;
    this.now = now;
    this.publishStatus = publishStatus;
    this.startupTimeoutMs = startupTimeoutMs;
    this.pollIntervalMs = pollIntervalMs;
    this.commandTimeoutMs = commandTimeoutMs;
  }

  /** @param {RuntimeStatus} status @returns {Promise<RuntimeStatus>} */
  async publish(status) {
    try {
      await this.publishStatus(status);
    } catch {
      // A closed launcher must not prevent a safe runtime transition.
    }
    return status;
  }

  /** @returns {Promise<{ composeVersion: string, engine: "linux" }>} */
  async preflight() {
    return this.dockerCli.preflight();
  }

  /** @returns {Promise<ProbeResult>} */
  async probe() {
    const result = await this.probeRuntime({
      fetchImpl: this.fetchImpl,
      apiOrigin: this.apiOrigin,
      webOrigin: this.webOrigin,
      timeoutMs: this.pollIntervalMs,
    });
    if (!result || !isEndpointState(result.api) || !isEndpointState(result.web)) {
      throw new Error("invalid runtime probe result");
    }
    return result;
  }

  /** @returns {Promise<Record<ServiceName, ServiceState>>} */
  async inspectCompose() {
    return this.inspectComposeProject({
      dockerCli: this.dockerCli,
      paths: this.paths,
      projectName: this.projectName,
      timeoutMs: this.commandTimeoutMs,
    });
  }

  /** @param {RuntimeStatus} status @param {unknown} error @returns {Promise<RuntimeStatus>} */
  async failForDockerError(status, error) {
    const code = codeFromDockerError(error);
    return this.publish(createStatus(status.phase === "checking" ? "needs-action" : "failed", null, code, messageForCode(code), status.services));
  }

  /** @returns {Promise<RuntimeStatus>} */
  async inspect() {
    let status = await this.publish(createStatus("checking", null, null, "กำลังตรวจสอบ Docker และสถานะบริการ", emptyServices()));
    try {
      await this.preflight();
    } catch (/** @type {unknown} */ error) {
      return this.failForDockerError(status, error);
    }

    let probe;
    try {
      probe = await this.probe();
    } catch {
      return this.publish(createStatus("failed", null, "invalid-runtime", messageForCode("invalid-runtime"), status.services));
    }

    let services;
    try {
      services = await this.inspectCompose();
    } catch (error) {
      const code = isInvalidComposeSnapshot(error)
        ? "invalid-runtime"
        : codeFromDockerError(error);
      return this.publish(createStatus("failed", null, code, messageForCode(code), status.services));
    }
    services = mergeEndpointStates(services, probe);

    if (probe.api === "foreign" || probe.web === "foreign") {
      return this.publish(createStatus("failed", null, "port-conflict", messageForCode("port-conflict"), services));
    }
    if (probe.api === "healthy" && probe.web === "healthy") {
      return this.publish(createStatus("ready", "attached", null, "เชื่อมต่อกับ Video Studio ที่พร้อมใช้งานแล้ว", services));
    }
    if (probe.api === "healthy" || probe.web === "healthy") {
      return this.publish(createStatus("needs-action", "recover-required", "partial-runtime", messageForCode("partial-runtime"), services));
    }
    if (noContainers(services) || allServicesAre(services, "stopped")) {
      status = createStatus("stopped", null, null, "Video Studio ยังไม่ได้เริ่มทำงาน", services);
      return this.publish(status);
    }
    return this.publish(createStatus("needs-action", "recover-required", "partial-runtime", messageForCode("partial-runtime"), services));
  }

  /** @param {string[]} subcommandArgs @returns {string[]} */
  composeCommand(subcommandArgs) {
    return composeArgs(this.paths, this.projectName, subcommandArgs);
  }

  /** @param {string[]} subcommandArgs @returns {Promise<RuntimeCode | null>} */
  async runCompose(subcommandArgs) {
    try {
      const result = await this.dockerCli.run(this.composeCommand(subcommandArgs), { timeoutMs: this.commandTimeoutMs });
      if (result.exitCode === 0) return null;
      return isKnownPortBindFailure(`${result.stdout}\n${result.stderr}`) ? "port-conflict" : "compose-failed";
    } catch (error) {
      return codeFromDockerError(error);
    }
  }

  /** @param {RuntimeStatus} status @returns {Promise<RuntimeStatus>} */
  /** @param {RuntimeStatus} status @param {RuntimeCode | null} code @returns {Promise<RuntimeStatus>} */
  async finishComposeFailure(status, code) {
    if (!code) return status;
    return this.publish(createStatus("failed", null, code, messageForCode(code), status.services));
  }

  /** @param {Record<ServiceName, ServiceState>} initialServices @returns {Promise<RuntimeStatus>} */
  async waitForHealthy(initialServices) {
    const deadline = this.now() + this.startupTimeoutMs;
    let services = { ...initialServices };

    while (true) {
      let probe;
      try {
        probe = await this.probe();
      } catch {
        return this.publish(createStatus("failed", null, "invalid-runtime", messageForCode("invalid-runtime"), services));
      }
      services = mergeEndpointStates(services, probe, true);

      if (probe.api === "foreign" || probe.web === "foreign") {
        return this.publish(createStatus("failed", null, "port-conflict", messageForCode("port-conflict"), services));
      }
      if (probe.api === "healthy" && probe.web === "healthy") {
        return this.publish(createStatus("ready", "started", null, "Video Studio พร้อมใช้งานแล้ว", services));
      }

      const remainingMs = deadline - this.now();
      if (remainingMs <= 0) {
        return this.publish(createStatus("failed", null, "health-timeout", messageForCode("health-timeout"), services));
      }
      await this.publish(createStatus("waiting-health", null, null, "กำลังรอให้บริการพร้อม", services));
      await this.sleep(Math.min(this.pollIntervalMs, remainingMs));
    }
  }

  /** @returns {Promise<RuntimeStatus>} */
  async start() {
    const current = await this.inspect();
    if (current.phase !== "stopped") return current;

    const startArgs = noContainers(current.services)
      ? ["up", "--detach", "--build"]
      : ["up", "--detach"];
    await this.publish(createStatus("starting", null, null, startArgs.includes("--build")
      ? "กำลังสร้างและเริ่ม Video Studio"
      : "กำลังเริ่ม Video Studio ที่หยุดไว้", current.services));

    const errorCode = await this.runCompose(startArgs);
    if (errorCode) return this.finishComposeFailure(current, errorCode);
    return this.waitForHealthy(current.services);
  }

  /** @returns {Promise<RuntimeStatus>} */
  async recover() {
    let status = await this.publish(createStatus("checking", null, null, "กำลังตรวจสอบ Docker ก่อนกู้คืน", emptyServices()));
    try {
      await this.preflight();
    } catch (error) {
      return this.failForDockerError(status, error);
    }

    await this.publish(createStatus("starting", null, null, "กำลังสร้างและกู้คืน Video Studio", status.services));
    const errorCode = await this.runCompose(["up", "--detach", "--build"]);
    if (errorCode) return this.finishComposeFailure(status, errorCode);
    status = await this.waitForHealthy(status.services);
    return status;
  }

  /** @returns {Promise<RuntimeStatus>} */
  async stop() {
    let status = await this.publish(createStatus("checking", null, null, "กำลังตรวจสอบ Docker ก่อนหยุด Engine", emptyServices()));
    try {
      await this.preflight();
    } catch (error) {
      return this.failForDockerError(status, error);
    }

    await this.publish(createStatus("checking", null, null, "กำลังหยุดบริการ Video Studio", status.services));
    const errorCode = await this.runCompose(["stop", "web", "worker", "api", "redis", "postgres"]);
    if (errorCode) return this.finishComposeFailure(status, errorCode);
    status = createStatus("stopped", "stopped", null, "หยุดบริการ Video Studio แล้ว ข้อมูลยังคงอยู่", uniformServices("stopped"));
    return this.publish(status);
  }
}

module.exports = { RuntimeManager, isKnownPortBindFailure };
