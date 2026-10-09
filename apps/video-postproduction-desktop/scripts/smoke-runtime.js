const path = require("node:path");
const { DockerCli } = require("../src/runtime/docker-cli");
const { RuntimeManager } = require("../src/runtime/manager");

const MINIMUM_COMPOSE_VERSION = Object.freeze([2, 24, 4]);
const SMOKE_PROJECT_NAME = "video-postproduction-native-smoke-test";
const SMOKE_API_ORIGIN = "http://127.0.0.1:18000";
const SMOKE_WEB_ORIGIN = "http://127.0.0.1:13400";
const EXPECTED_SMOKE_VOLUME_NAMES = Object.freeze([
  `${SMOKE_PROJECT_NAME}_postgres_data`,
  `${SMOKE_PROJECT_NAME}_redis_data`,
  `${SMOKE_PROJECT_NAME}_video_storage`,
  `${SMOKE_PROJECT_NAME}_whisper_cache`,
].sort());
const COMPOSE_VERSION_ARGS = Object.freeze(["compose", "version", "--short"]);
const VOLUME_LIST_ARGS = Object.freeze([
  "volume",
  "ls",
  "--filter",
  `label=com.docker.compose.project=${SMOKE_PROJECT_NAME}`,
  "--format",
  "{{.Name}}",
]);

/** @typedef {{ exitCode: number, stdout: string, stderr: string }} DockerResult */
/** @typedef {{ status: "skipped", composeVersion: string, reason: string } | { status: "passed", composeVersion: string, volumeNames: string[] }} SmokeResult */
/** @typedef {{ phase?: string, action?: string | null, services?: { api?: string, web?: string } }} SmokeRuntimeStatus */
/** @typedef {{ start: () => Promise<SmokeRuntimeStatus>, stop: () => Promise<SmokeRuntimeStatus> }} SmokeRuntimeManager */
/** @typedef {ConstructorParameters<typeof RuntimeManager>[0]} RuntimeManagerOptions */
/** @typedef {(options: RuntimeManagerOptions) => SmokeRuntimeManager} SmokeRuntimeManagerFactory */

/** @param {string} output @returns {{ display: string, parts: number[], prerelease: boolean }} */
function parseComposeVersion(output) {
  const match = String(output ?? "").trim().match(
    /^(?:Docker Compose version\s+)?v?(\d+)\.(\d+)\.(\d+)(-[-0-9A-Za-z.]+)?(?:\+[0-9A-Za-z.-]+)?$/i,
  );
  if (!match) throw new Error("อ่านเวอร์ชัน Docker Compose ไม่สำเร็จ");

  const parts = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (parts.some((part) => !Number.isSafeInteger(part))) {
    throw new Error("อ่านเวอร์ชัน Docker Compose ไม่สำเร็จ");
  }
  return { display: parts.join("."), parts, prerelease: Boolean(match[4]) };
}

/** @param {{ parts: number[], prerelease: boolean }} version @returns {boolean} */
function meetsMinimumComposeVersion(version) {
  for (let index = 0; index < MINIMUM_COMPOSE_VERSION.length; index += 1) {
    const required = MINIMUM_COMPOSE_VERSION[index];
    const actual = version.parts[index] ?? 0;
    if (actual > required) return true;
    if (actual < required) return false;
  }
  return !version.prerelease;
}

/** @param {DockerResult} result @returns {string[]} */
function parseSmokeVolumeNames(result) {
  if (result.exitCode !== 0) throw new Error("ตรวจสอบ named volume ของ smoke project ไม่สำเร็จ");
  const names = result.stdout.split(/\r?\n/).map((name) => name.trim()).filter(Boolean).sort();
  if (names.length === 0 || new Set(names).size !== names.length) {
    throw new Error("ไม่พบรายการ named volume ที่ถูกต้องของ smoke project");
  }
  if (names.length !== EXPECTED_SMOKE_VOLUME_NAMES.length
    || names.some((name, index) => name !== EXPECTED_SMOKE_VOLUME_NAMES[index])) {
    throw new Error("smoke project volume set does not match the expected contract");
  }
  return names;
}

/** @param {SmokeRuntimeStatus} status */
function requireHealthySmokeRuntime(status) {
  if (status?.phase !== "ready" || status.services?.api !== "healthy" || status.services?.web !== "healthy") {
    throw new Error("smoke start did not verify both API and web identities; ตรวจสอบ smoke project ด้วยตนเองก่อนลองใหม่");
  }
  if (status.action !== "started") {
    throw new Error("smoke start did not start this smoke project; จะไม่หยุดบริการที่มีอยู่ก่อนแล้ว");
  }
}

/** @param {SmokeRuntimeStatus} status */
function requireStoppedSmokeRuntime(status) {
  if (status?.phase !== "stopped") {
    throw new Error("หยุด smoke project ไม่สำเร็จ ตรวจสอบสถานะก่อนดำเนินการต่อ");
  }
}

/** @param {{ dockerCli?: RuntimeManagerOptions["dockerCli"], createRuntimeManager?: SmokeRuntimeManagerFactory }} [dependencies] @returns {Promise<SmokeResult>} */
async function runSmokeRuntime({
  dockerCli = new DockerCli(),
  createRuntimeManager = (options) => new RuntimeManager(options),
} = {}) {
  const composeVersionResult = await dockerCli.run([...COMPOSE_VERSION_ARGS]);
  if (composeVersionResult.exitCode !== 0) {
    throw new Error("ตรวจสอบเวอร์ชัน Docker Compose ไม่สำเร็จ");
  }
  const composeVersion = parseComposeVersion(composeVersionResult.stdout || composeVersionResult.stderr);
  if (!meetsMinimumComposeVersion(composeVersion)) {
    return {
      status: "skipped",
      composeVersion: composeVersion.display,
      reason: "ต้องใช้ Docker Compose 2.24.4 ขึ้นไปเพื่อรองรับ !override",
    };
  }

  const packageRoot = path.resolve(__dirname, "..");
  const runtimeRoot = path.join(packageRoot, "dist", "video-runtime");
  const composeProjectDirectory = path.join(runtimeRoot, "services", "video-postproduction");
  const composeFile = path.join(composeProjectDirectory, "docker-compose.yml");
  const overrideFile = path.join(packageRoot, "tests", "fixtures", "docker-compose.native-smoke.override.yml");
  const manager = createRuntimeManager({
    dockerCli,
    paths: {
      composeFile,
      composeFiles: [composeFile, overrideFile],
      composeProjectDirectory,
    },
    projectName: SMOKE_PROJECT_NAME,
    apiOrigin: SMOKE_API_ORIGIN,
    webOrigin: SMOKE_WEB_ORIGIN,
  });

  const firstStart = await manager.start();
  requireHealthySmokeRuntime(firstStart);

  let firstVolumeNames;
  let firstVolumeError;
  try {
    firstVolumeNames = parseSmokeVolumeNames(await dockerCli.run([...VOLUME_LIST_ARGS]));
  } catch (error) {
    firstVolumeError = error;
  }
  requireStoppedSmokeRuntime(await manager.stop());
  if (firstVolumeError) throw firstVolumeError;
  if (!firstVolumeNames) throw new Error("ไม่พบรายการ named volume ที่ถูกต้องของ smoke project");

  const secondStart = await manager.start();
  requireHealthySmokeRuntime(secondStart);

  let secondVolumeNames;
  let secondVolumeError;
  try {
    secondVolumeNames = parseSmokeVolumeNames(await dockerCli.run([...VOLUME_LIST_ARGS]));
  } catch (error) {
    secondVolumeError = error;
  }
  requireStoppedSmokeRuntime(await manager.stop());
  if (secondVolumeError) throw secondVolumeError;
  if (!secondVolumeNames) throw new Error("ไม่พบรายการ named volume ที่ถูกต้องของ smoke project");
  if (JSON.stringify(firstVolumeNames) !== JSON.stringify(secondVolumeNames)) {
    throw new Error("smoke project volume set changed after second start");
  }

  return { status: "passed", composeVersion: composeVersion.display, volumeNames: firstVolumeNames };
}

if (require.main === module) {
  runSmokeRuntime()
    .then((result) => {
      if (result.status === "skipped") {
        console.log(`SKIPPED: ${result.reason} (พบ Compose ${result.composeVersion})`);
      } else {
        console.log(`ผ่าน: smoke project พร้อมใช้งานสองรอบและรักษา named volume ${result.volumeNames.length} รายการ`);
      }
    })
    .catch((error) => {
      const message = error instanceof Error ? error.message : "runtime smoke ล้มเหลว";
      console.error(`ไม่ผ่าน: ${message}`);
      process.exitCode = 1;
    });
}

module.exports = { runSmokeRuntime };
