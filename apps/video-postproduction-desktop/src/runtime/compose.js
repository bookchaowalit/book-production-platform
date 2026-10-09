const { DockerCliError } = require("./docker-cli");
const { PROJECT_NAME, SERVICE_NAMES } = require("./constants");

const SMOKE_PROJECT_NAME = "video-postproduction-native-smoke-test";

/** @typedef {"postgres" | "redis" | "api" | "worker" | "web"} ServiceName */
/** @typedef {"unknown" | "running" | "healthy" | "stopped" | "unhealthy"} ServiceState */
/** @typedef {Record<ServiceName, ServiceState>} ComposeSnapshot */
/** @typedef {{ composeFile: string, composeFiles?: string[], composeProjectDirectory: string }} RuntimePaths */
/** @typedef {{ run: (args: string[], options?: { timeoutMs?: number }) => Promise<{ exitCode: number, stdout: string, stderr: string }> }} DockerCli */

/**
 * @param {RuntimePaths} paths
 * @param {string} projectName
 * @param {string[]} subcommandArgs
 * @returns {string[]}
 */
function composeArgs(paths, projectName = PROJECT_NAME, subcommandArgs = []) {
  if (projectName !== PROJECT_NAME && projectName !== SMOKE_PROJECT_NAME) {
    throw new Error("invalid Compose project name");
  }
  const composeFiles = paths.composeFiles ?? [paths.composeFile];
  if (
    !Array.isArray(composeFiles)
    || composeFiles.length === 0
    || composeFiles.some((file) => typeof file !== "string" || file.length === 0)
    || typeof paths.composeProjectDirectory !== "string"
    || paths.composeProjectDirectory.length === 0
  ) {
    throw new Error("invalid Compose paths");
  }
  if (projectName === PROJECT_NAME && paths.composeFiles !== undefined) {
    throw new Error("invalid Compose file profile");
  }
  if (
    projectName === SMOKE_PROJECT_NAME
    && (
      composeFiles.length !== 2
      || composeFiles[0] !== paths.composeFile
      || !/(?:^|[\\/])tests[\\/]fixtures[\\/]docker-compose\.native-smoke\.override\.yml$/i.test(composeFiles[1])
    )
  ) {
    throw new Error("invalid Compose file profile");
  }
  if (!Array.isArray(subcommandArgs) || subcommandArgs.some((argument) => typeof argument !== "string")) {
    throw new Error("invalid Compose arguments");
  }

  return [
    "compose", "--project-name", projectName,
    ...composeFiles.flatMap((file) => ["--file", file]),
    "--project-directory", paths.composeProjectDirectory,
    ...subcommandArgs,
  ];
}

/** @returns {ComposeSnapshot} */
function emptySnapshot() {
  return /** @type {ComposeSnapshot} */ (Object.fromEntries(SERVICE_NAMES.map((service) => [service, "unknown"])));
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** @param {string} state @param {unknown} health @returns {ServiceState} */
function mapServiceState(state, health) {
  const normalizedState = state.trim().toLowerCase();
  if (normalizedState === "running") {
    if (health === undefined || health === null || health === "" || health === "none" || health === "starting") {
      return "running";
    }
    if (typeof health !== "string") throw new Error("invalid Compose status output");
    const normalizedHealth = health.trim().toLowerCase();
    if (normalizedHealth === "healthy") return "healthy";
    if (normalizedHealth === "unhealthy") return "unhealthy";
    if (normalizedHealth === "starting" || normalizedHealth === "none" || normalizedHealth === "") return "running";
    throw new Error("invalid Compose status output");
  }
  if (["created", "exited", "dead", "removing", "restarting", "paused", "stopped"].includes(normalizedState)) {
    return "stopped";
  }
  throw new Error("invalid Compose status output");
}

/** @param {string} output @returns {ComposeSnapshot} */
function parseComposePs(output) {
  const snapshot = emptySnapshot();
  const seen = new Set();

  for (const line of String(output ?? "").split(/\r?\n/).filter((value) => value.trim().length > 0)) {
    /** @type {unknown} */
    let value;
    try {
      value = JSON.parse(line);
    } catch {
      throw new Error("invalid Compose status output");
    }
    if (!isRecord(value) || typeof value.Service !== "string" || typeof value.State !== "string") {
      throw new Error("invalid Compose status output");
    }
    if (!SERVICE_NAMES.includes(value.Service)) throw new Error("unexpected Compose service");
    if (seen.has(value.Service)) throw new Error("duplicate Compose service");
    seen.add(value.Service);

    snapshot[/** @type {ServiceName} */ (value.Service)] = mapServiceState(value.State, value.Health);
  }

  return snapshot;
}

/**
 * @param {{ dockerCli: DockerCli, paths: RuntimePaths, projectName?: string, timeoutMs?: number }} options
 * @returns {Promise<ComposeSnapshot>}
 */
async function inspectComposeProject({ dockerCli, paths, projectName = PROJECT_NAME, timeoutMs }) {
  const args = composeArgs(paths, projectName, ["ps", "--all", "--format", "json"]);
  const result = await dockerCli.run(args, timeoutMs === undefined ? {} : { timeoutMs });
  if (result.exitCode !== 0) {
    throw new DockerCliError("compose-failed", "ตรวจสอบสถานะ Docker Compose ไม่สำเร็จ");
  }
  return parseComposePs(result.stdout);
}

module.exports = { composeArgs, inspectComposeProject, parseComposePs };
