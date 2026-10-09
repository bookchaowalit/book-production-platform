const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MAX_OUTPUT_BYTES = 64 * 1024;

/**
 * @typedef {import("node:events").EventEmitter & {
 *   stdout: import("node:stream").Readable,
 *   stderr: import("node:stream").Readable,
 *   kill: (signal?: NodeJS.Signals) => boolean
 * }} DockerChild
 * @typedef {(executable: string, args: string[], options: {
 *   shell: false,
 *   windowsHide: true,
 *   stdio: ["ignore", "pipe", "pipe"]
 * }) => DockerChild} SpawnImpl
 * @typedef {{
 *   exitCode: number,
 *   signal: NodeJS.Signals | null,
 *   stdout: string,
 *   stderr: string
 * }} DockerRunResult
 * @typedef {{
 *   executable?: string | null,
 *   platform?: NodeJS.Platform,
 *   pathValue?: string,
 *   programFiles?: string,
 *   pathEntries?: string[],
 *   exists?: (candidate: string) => boolean,
 *   spawnImpl?: SpawnImpl,
 *   timeoutMs?: number
 * }} DockerCliOptions
 */

class DockerCliError extends Error {
  /** @param {string} code @param {string} message */
  constructor(code, message) {
    super(message);
    this.name = "DockerCliError";
    this.code = code;
  }
}

/**
 * @param {{
 *   platform?: NodeJS.Platform,
 *   pathEntries?: string[],
 *   programFiles?: string,
 *   exists?: (candidate: string) => boolean
 * }} options
 * @returns {string | null}
 */
function findDockerExecutable({
  platform = process.platform,
  pathEntries = [],
  programFiles,
  exists = fs.existsSync,
}) {
  const pathApi = platform === "win32" ? path.win32 : path.posix;
  const executableName = platform === "win32" ? "docker.exe" : "docker";
  const candidates = pathEntries
    .filter((entry) => typeof entry === "string" && entry.length > 0)
    .map((entry) => pathApi.join(entry, executableName));

  if (platform === "win32" && programFiles) {
    candidates.push(path.win32.join(programFiles, "Docker", "Docker", "resources", "bin", "docker.exe"));
  }

  for (const candidate of candidates) {
    try {
      if (exists(candidate)) return candidate;
    } catch {
      // An inaccessible PATH entry is equivalent to a missing executable there.
    }
  }
  return null;
}

/** @param {Buffer} current @param {Buffer | string} chunk @returns {Buffer} */
function appendTail(current, chunk) {
  const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
  const combined = Buffer.concat([current, bytes]);
  return combined.length > MAX_OUTPUT_BYTES
    ? combined.subarray(combined.length - MAX_OUTPUT_BYTES)
    : combined;
}

/** @param {NodeJS.Platform} platform @param {string} pathValue @returns {string[]} */
function readPathEntries(platform, pathValue) {
  const delimiter = platform === "win32" ? ";" : ":";
  return String(pathValue ?? "").split(delimiter).filter(Boolean);
}

class DockerCli {
  /** @param {DockerCliOptions} [options] */
  constructor({
    executable,
    platform = process.platform,
    pathValue = process.env.PATH ?? process.env.Path ?? "",
    programFiles = process.env.ProgramFiles,
    pathEntries,
    exists = fs.existsSync,
    spawnImpl = spawn,
    timeoutMs = 30_000,
  } = {}) {
    this.platform = platform;
    this.executable = executable === undefined
      ? findDockerExecutable({
        platform,
        pathEntries: pathEntries ?? readPathEntries(platform, pathValue),
        programFiles,
        exists,
      })
      : executable;
    this.spawnImpl = spawnImpl;
    this.timeoutMs = timeoutMs;
  }

  /** @param {string[]} args @param {{ timeoutMs?: number }} [options] @returns {Promise<DockerRunResult>} */
  run(args, { timeoutMs = this.timeoutMs } = {}) {
    const executable = this.executable;
    if (!executable) {
      return Promise.reject(new DockerCliError("missing-docker", "ไม่พบ Docker Desktop หรือ Docker CLI ใน PATH"));
    }
    if (!Array.isArray(args) || args.some((argument) => typeof argument !== "string")) {
      return Promise.reject(new DockerCliError("compose-failed", "คำสั่ง Docker ที่กำหนดไม่ถูกต้อง"));
    }
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return Promise.reject(new DockerCliError("compose-failed", "กำหนดเวลารอคำสั่ง Docker ไม่ถูกต้อง"));
    }

    return new Promise((resolve, reject) => {
      let child;
      try {
        child = this.spawnImpl(executable, args, {
          shell: false,
          windowsHide: true,
          stdio: ["ignore", "pipe", "pipe"],
        });
      } catch {
        reject(new DockerCliError("engine-unavailable", "เปิดใช้งาน Docker CLI ไม่สำเร็จ"));
        return;
      }

      /** @type {Buffer} */
      let stdout = Buffer.alloc(0);
      /** @type {Buffer} */
      let stderr = Buffer.alloc(0);
      let settled = false;
      /** @type {NodeJS.Timeout | undefined} */
      let timer;

      /** @param {DockerCliError} error */
      const finishWithError = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      };
      /** @param {DockerRunResult} result */
      const finishWithResult = (result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(result);
      };

      child.stdout.on("data", (/** @type {Buffer | string} */ chunk) => { stdout = appendTail(stdout, chunk); });
      child.stderr.on("data", (/** @type {Buffer | string} */ chunk) => { stderr = appendTail(stderr, chunk); });
      child.once("error", (/** @type {NodeJS.ErrnoException} */ error) => {
        const code = error?.code === "ENOENT" ? "missing-docker" : "engine-unavailable";
        const message = code === "missing-docker"
          ? "ไม่พบ Docker CLI ตามตำแหน่งที่ตรวจสอบ"
          : "Docker CLI หรือ Docker Engine พร้อมใช้งานไม่ได้";
        finishWithError(new DockerCliError(code, message));
      });
      child.once("close", (/** @type {number | null} */ exitCode, /** @type {NodeJS.Signals | null} */ signal) => {
        finishWithResult({
          exitCode: typeof exitCode === "number" && Number.isInteger(exitCode) ? exitCode : -1,
          signal: signal ?? null,
          stdout: stdout.toString("utf8"),
          stderr: stderr.toString("utf8"),
        });
      });

      timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {
          // The caller receives the stable timeout failure even if termination fails.
        }
        finishWithError(new DockerCliError("compose-failed", "คำสั่ง Docker ใช้เวลานานเกินกำหนด"));
      }, timeoutMs);
    });
  }

  /** @returns {Promise<{ composeVersion: string, engine: "linux" }>} */
  async preflight() {
    const composeResult = await this.run(["compose", "version"]);
    if (composeResult.exitCode !== 0) {
      throw new DockerCliError("compose-failed", "ต้องติดตั้ง Docker Compose รุ่น v2 หรือ v5 ก่อนใช้งาน");
    }

    const versionText = `${composeResult.stdout}\n${composeResult.stderr}`;
    const versionMatch = versionText.match(/\bCompose version\s+v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\b/i);
    if (!versionMatch || ![2, 5].includes(Number(versionMatch[1]))) {
      throw new DockerCliError("compose-failed", "ต้องติดตั้ง Docker Compose รุ่น v2 หรือ v5 ก่อนใช้งาน");
    }
    const composeVersion = `${versionMatch[1]}.${versionMatch[2]}.${versionMatch[3]}`;

    const engineResult = await this.run(["info", "--format", "{{.OSType}}"]);
    if (engineResult.exitCode !== 0 || engineResult.stdout.trim().toLowerCase() !== "linux") {
      throw new DockerCliError("engine-unavailable", "Docker Engine ยังไม่พร้อมหรือไม่ได้ใช้ Linux containers");
    }

    return { composeVersion, engine: "linux" };
  }
}

module.exports = { DockerCli, DockerCliError, findDockerExecutable };
