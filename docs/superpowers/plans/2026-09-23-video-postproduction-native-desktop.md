# Native Windows Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. The main agent owns scope, shared integration, final verification, and delivery; delegate only independent file groups, with no child agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ส่งมอบ Windows desktop installer ที่เปิด Video Post-Production ผ่าน Electron และเชื่อมกับ Docker Compose runtime/data เดิมได้อย่างปลอดภัย

**Architecture:** เพิ่ม product package แยกที่ `apps/video-postproduction-desktop/` ใช้ Electron main process ตรวจและจัดการ Docker Compose ส่วนหน้า launcher ขนาดเล็กใช้ preload API แบบ allowlist; หน้า editor เดิมเปิดจาก `127.0.0.1:3400` โดยไม่มี preload หรือ Node access. Installer ฝัง runtime source bundle แบบ allowlist และใช้ Compose project name `video-postproduction` เพื่อเชื่อม named volumes เดิม.

**Tech Stack:** Electron, Electron Forge + Squirrel.Windows, CommonJS JavaScript พร้อม JSDoc และ TypeScript `checkJs`, Node built-in `node:test`, Docker Compose v2. ใช้ stable versions ที่ยังได้รับ security updates ในวันที่ implement และ pin exact versions ใน `package-lock.json`; ไม่ใช้ native Node modules หรือ Vite plugin.

**Spec:** `docs/superpowers/specs/2026-09-23-video-postproduction-native-desktop-design.md`

## Global Constraints

- Native V1 เป็น Windows x64 per-user Electron app; Docker Desktop พร้อม Linux container engine และ Docker Compose v2 เป็น prerequisite ที่ผู้ใช้ติดตั้งเอง
- Compose project name ต้องเป็น `video-postproduction`; API/UI ports คือ `127.0.0.1:8000` และ `127.0.0.1:3400`
- เมื่อ API และ web ของ runtime เดิม healthy แล้ว ห้ามสั่ง `up`, `restart`, `down` หรือ build image
- การปิด app ห้ามหยุด Compose; คำสั่งหยุดจากผู้ใช้ใช้ Compose `stop` เท่านั้นและห้ามลบ named volumes
- ห้าม execute shell string หรือรับ executable/argument จาก renderer; child process ทุกตัวใช้ fixed executable และ argument array โดย `shell: false`
- Launcher renderer ใช้ `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`; Studio window ไม่มี preload
- ห้าม bundle `.env`, credentials, user data, media, `.next`, `node_modules` หรือ `.git` ใน runtime bundle/installer
- ใช้ `git add -- <scoped paths>` เท่านั้น; worktree มีงานอื่นที่ไม่เกี่ยวข้องและต้องเก็บไว้
- Main agent เป็นผู้วางแผนและรวมงาน; มอบหมาย role ที่ลงทะเบียนไว้เฉพาะงานไฟล์ที่ไม่ทับกัน; subagent ส่ง structured handoff และไม่สร้าง child agent; main เป็นผู้ตรวจ/commit เฉพาะ scope นี้
- ไม่ซื้อ certificate, ไม่ publish installer, ไม่เปลี่ยน production และไม่ push branch ที่มี commits นอก scope

## Review Focus

- Docker CLI หาย, Docker Desktop ปิด, Linux engine ไม่พร้อม หรือ path มีช่องว่างต้องให้ข้อความแก้ได้โดยไม่เปิด shell — ทดสอบใน Task 2 ด้วย `test_missing_cli_is_typed_error`, `test_engine_must_report_linux`, และ `test_path_with_spaces_remains_one_argument`
- port 8000/3400 ถูกใช้โดย service อื่นหรือคืน payload ผิดชนิดต้องถูกจัดเป็น `port-conflict` และห้ามเปิดหน้าเว็บนั้น — ทดสอบใน Task 3 ด้วย `test_wrong_api_identity_is_foreign`/`test_wrong_web_title_is_foreign` และใน Task 4 ด้วยการแปลง Docker bind error ที่รู้จักเป็น port conflict
- `docker compose ps --format json` อาจคืน JSON ต่อ container คนละบรรทัด, ไม่มี container, ทั้งหมด stopped หรือมี partial stack; parser ต้องแยกทุกกรณี — ทดสอบใน Task 3 ด้วย fixture แต่ละรูปแบบ
- build/start/health timeout/recover/stop ล้มเหลวต้องไม่เรียก `down`, `down -v` หรือหยุด services เงียบ ๆ; named volumes ต้องคงเดิม — ทดสอบใน Task 4 ด้วย `test_failed_start_never_runs_cleanup`, `test_recover_never_removes_volumes`, และ `test_stop_uses_compose_stop_only`
- iframe, child window, redirect, invalid custom-protocol path หรือ IPC จาก Studio window ต้องไม่ได้สิทธิ์ main process — ทดสอบใน Task 5 ด้วย `test_untrusted_sender_is_rejected`, `test_external_navigation_is_blocked`, และ `test_launcher_protocol_rejects_path_traversal`

---

## Planned file map

### Desktop package

- Create `apps/video-postproduction-desktop/package.json` และ `package-lock.json` — scripts, pinned dependencies, Squirrel metadata
- Create `apps/video-postproduction-desktop/tsconfig.json` — strict JSDoc type checking โดยไม่ transpile app
- Create `apps/video-postproduction-desktop/forge.config.js` — Windows x64 Squirrel maker และ packaged resources
- Create `apps/video-postproduction-desktop/src/runtime/constants.js` — service names, ports, project identity และ status codes
- Create `apps/video-postproduction-desktop/src/runtime/types.d.ts` — canonical runtime/status types shared with the launcher bridge
- Create `apps/video-postproduction-desktop/src/runtime/paths.js` — resolve development/packaged runtime paths
- Create `apps/video-postproduction-desktop/src/runtime/docker-cli.js` — locate `docker.exe`, run bounded child process และ sanitize diagnostic
- Create `apps/video-postproduction-desktop/src/runtime/compose.js` — fixed Compose command arguments และ `ps` parser
- Create `apps/video-postproduction-desktop/src/runtime/health.js` — probe API/web identity ด้วย timeout
- Create `apps/video-postproduction-desktop/src/runtime/manager.js` — attach/start/recover/stop state machine
- Create `apps/video-postproduction-desktop/src/security/window-policy.js` — origin, navigation, popup, sender และ CSP policy
- Create `apps/video-postproduction-desktop/src/security/window-options.js` — safe BrowserWindow preferences with launcher-only preload
- Create `apps/video-postproduction-desktop/src/electron/lifecycle.js` — single-instance, Squirrel exit, and confirmation-gated shutdown policy
- Create `apps/video-postproduction-desktop/src/protocol/launcher-protocol.js` — serve only packaged launcher files through a secure custom protocol
- Create `apps/video-postproduction-desktop/src/security/launcher-bridge.js` — pure allowlisted preload API factory
- Create `apps/video-postproduction-desktop/src/main.js` และ `src/preload.js` — Electron lifecycle, narrow launcher bridge, native menu
- Create `apps/video-postproduction-desktop/src/renderer.d.ts` — strict `window.videoStudio` bridge type for the launcher only
- Create `apps/video-postproduction-desktop/src/launcher/index.html`, `launcher.js`, `launcher.css` — Thai preflight/progress/retry/recover UI
- Create `apps/video-postproduction-desktop/scripts/runtime-bundle-manifest.js` และ `stage-runtime.js` — allowlisted, reproducible Compose build context
- Create `apps/video-postproduction-desktop/scripts/smoke-runtime.js` — opt-in isolated Compose/health lifecycle acceptance, never using the production project name
- Create `apps/video-postproduction-desktop/scripts/verify-artifacts.js` — validate the installer output stays under the configured Forge output directory
- Create `apps/video-postproduction-desktop/tests/` — unit, lifecycle, security, packaging contract tests
- Create `apps/video-postproduction-desktop/tests/helpers/fake-runtime.js` — deterministic Docker/health fixtures for manager tests
- Create `apps/video-postproduction-desktop/tests/helpers/runtime-fixture.js` — isolated filesystem fixture for bundle tests
- Create `apps/video-postproduction-desktop/tests/fixtures/docker-compose.native-smoke.override.yml` — isolated local runtime test using separate image tags, project and ports

### Existing repository-owned docs and command adapter

- Modify `apps/README.md` — register the desktop package and supported launch/build commands
- Modify `services/video-postproduction/README.md` — explain desktop prerequisite, existing data reuse, stop behavior and unsigned installer warning
- Modify `infra/tasks/products.yml` — add thin Task wrappers for desktop test/typecheck/make; implementation remains in the desktop package

The existing `services/video-postproduction/docker-compose.yml`, API and web runtime
remain the source of truth. The desktop app passes the exact Compose file and sets
`--project-directory` to `dirname(composeFile)` so the current `build.context: ../..`
still resolves to the runtime bundle root.

## Interfaces locked by this plan

The following names and shapes are shared between tasks. Keep them stable; if a
change is required, update this block and affected downstream tests first.

```js
// src/runtime/constants.js
const PROJECT_NAME = "video-postproduction";
const SERVICE_NAMES = ["postgres", "redis", "api", "worker", "web"];
const API_ORIGIN = "http://127.0.0.1:8000";
const WEB_ORIGIN = "http://127.0.0.1:3400";

// RuntimeStatus is JSON-serializable and contains no raw command output.
// phase: checking | starting | waiting-health | ready | needs-action | stopped | failed
// action: attached | started | recover-required | stopped | null
// code: missing-docker | engine-unavailable | port-conflict | partial-runtime |
//       compose-failed | health-timeout | invalid-runtime | null
// services: map of the five fixed service names to unknown | running | healthy |
//           stopped | unhealthy
//
// JSDoc unions used across modules:
// ServiceName = "postgres" | "redis" | "api" | "worker" | "web"
// ServiceState = "unknown" | "running" | "healthy" | "stopped" | "unhealthy"
// RuntimePhase = "checking" | "starting" | "waiting-health" | "ready" |
//                "needs-action" | "stopped" | "failed"
// RuntimeAction = "attached" | "started" | "recover-required" | "stopped" | null
// RuntimeCode = "missing-docker" | "engine-unavailable" | "port-conflict" |
//               "partial-runtime" | "compose-failed" | "health-timeout" |
//               "invalid-runtime" | null
// ProbeState = "healthy" | "unavailable" | "foreign"
// RuntimeStatus = { phase: RuntimePhase, action: RuntimeAction, code: RuntimeCode,
//   message: string, services: Record<ServiceName, ServiceState> }
// Canonical exported definitions live in src/runtime/types.d.ts. JSDoc in JS
// imports them instead of duplicating structural types. Messages are fixed Thai
// strings and never contain raw Docker output.
// RuntimeConfig = { projectName: "video-postproduction" |
//   "video-postproduction-native-smoke-test", apiOrigin: string, webOrigin: string,
//   healthTimeoutMs: number, healthPollMs: number, startupTimeoutMs: number }
// Production main.js constructs only production config; smoke-runtime.js constructs
// only the fixed test config. Neither configuration is renderer-controlled.

// src/runtime/paths.js
function resolveRuntimePaths({ isPackaged, appPath, resourcesPath, userDataPath }) {}
// returns { bundleRoot, composeFile, composeProjectDirectory, userDataPath }

// src/runtime/docker-cli.js
class DockerCli {
  constructor({ executable, spawnImpl, maxDiagnosticBytes }) {}
  run(args, { timeoutMs }) {} // Promise<{ code, stdoutTail, stderrTail }>
  preflight() {} // Promise<{ ok, code, message, composeVersion }>
}

// src/runtime/compose.js
function composeArgs(paths, projectName, subcommandArgs) {}
// paths may include composeFiles?: string[] only for the fixed smoke override
function parseComposePs(stdout) {} // validated service snapshot; accepts NDJSON

// src/runtime/health.js
async function probeRuntime({ fetchImpl, apiOrigin, webOrigin, timeoutMs }) {}
// returns { api: healthy | unavailable | foreign, web: healthy | unavailable | foreign }

// src/runtime/manager.js
class RuntimeManager {
  constructor({ paths, docker, inspectProject, probe, runCompose, publishStatus, sleep, now, config }) {}
  inspect() {}  // Promise<RuntimeStatus>
  start() {}    // attach if healthy; start if absent/all stopped; require confirmation if partial
  recover() {}  // explicit user action; compose up --detach --build, never removes volumes
  stop() {}     // explicit user action; compose stop only
}

// src/preload.js exposes exactly these methods to launcher origin only:
// window.videoStudio.getStatus(): Promise<RuntimeStatus>
// window.videoStudio.start(): Promise<RuntimeStatus>
// window.videoStudio.recover(): Promise<RuntimeStatus>
// window.videoStudio.onStatus(callback): () => void

// scripts/stage-runtime.js
async function stageRuntime({ repoRoot, packageRoot }) {}
// derives and writes only packageRoot/dist/video-runtime; returns its manifest/hash
```

### Task 1: Desktop package foundation and path resolution

**Files:**
- Create: `apps/video-postproduction-desktop/package.json`
- Create: `apps/video-postproduction-desktop/package-lock.json`
- Create: `apps/video-postproduction-desktop/tsconfig.json`
- Create: `apps/video-postproduction-desktop/src/runtime/constants.js`
- Create: `apps/video-postproduction-desktop/src/runtime/types.d.ts`
- Create: `apps/video-postproduction-desktop/src/runtime/paths.js`
- Test: `apps/video-postproduction-desktop/tests/paths.test.js`

**Interfaces:**
- Consumes: Electron package input values `appPath`, `resourcesPath`, `userDataPath`, and `isPackaged`.
- Produces: `PROJECT_NAME`, fixed service/origin constants, and `resolveRuntimePaths(...)` for every runtime task.

- [ ] **Step 1: Write the failing path tests**

```js
const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const { resolveRuntimePaths } = require("../src/runtime/paths");

test("packaged paths keep the Compose bundle and user data separate", () => {
  const appPath = path.join("C:", "Program Files", "Solo Empire Video Studio", "resources", "app.asar");
  const resourcesPath = path.join("C:", "Program Files", "Solo Empire Video Studio", "resources");
  const userDataPath = path.join("C:", "Users", "tester", "AppData", "Roaming", "video-studio");
  const paths = resolveRuntimePaths({ isPackaged: true, appPath, resourcesPath, userDataPath });

  assert.equal(paths.bundleRoot, path.join(resourcesPath, "video-runtime"));
  assert.equal(paths.composeFile, path.join(paths.bundleRoot, "services", "video-postproduction", "docker-compose.yml"));
  assert.equal(paths.composeProjectDirectory, path.dirname(paths.composeFile));
  assert.equal(paths.userDataPath, userDataPath);
  assert.notEqual(paths.bundleRoot, paths.userDataPath);
});

test("development paths point back to the repository root", () => {
  const appPath = path.join("C:", "work", "solo-empire", "apps", "video-postproduction-desktop");
  const paths = resolveRuntimePaths({ isPackaged: false, appPath, resourcesPath: "", userDataPath: "C:\\temp\\studio" });

  assert.equal(paths.bundleRoot, path.resolve(appPath, "..", ".."));
  assert.equal(paths.composeProjectDirectory, path.join(paths.bundleRoot, "services", "video-postproduction"));
});
```

- [ ] **Step 2: Run the focused tests and verify the failure**

Run from `apps/video-postproduction-desktop/`: `node --test tests/paths.test.js`
Expected: FAIL because `src/runtime/paths.js` does not exist.

- [ ] **Step 3: Add fixed constants, shared types, and the minimal path resolver**

`src/runtime/types.d.ts` exports the exact ServiceName, ServiceState, RuntimePhase,
RuntimeAction, RuntimeCode unions and RuntimeStatus object shape locked above. Keep
those declarations canonical; JavaScript consumers use JSDoc `import(...)` types.

```js
// src/runtime/constants.js
exports.PROJECT_NAME = "video-postproduction";
exports.SERVICE_NAMES = Object.freeze(["postgres", "redis", "api", "worker", "web"]);
exports.API_ORIGIN = "http://127.0.0.1:8000";
exports.WEB_ORIGIN = "http://127.0.0.1:3400";

// src/runtime/paths.js
const path = require("node:path");

/**
 * @param {{ isPackaged: boolean, appPath: string, resourcesPath: string, userDataPath: string }} input
 * @returns {{ bundleRoot: string, composeFile: string, composeProjectDirectory: string, userDataPath: string }}
 */
exports.resolveRuntimePaths = function resolveRuntimePaths(input) {
  const bundleRoot = input.isPackaged
    ? path.join(input.resourcesPath, "video-runtime")
    : path.resolve(input.appPath, "..", "..");
  const composeFile = path.join(bundleRoot, "services", "video-postproduction", "docker-compose.yml");
  return {
    bundleRoot,
    composeFile,
    composeProjectDirectory: path.dirname(composeFile),
    userDataPath: input.userDataPath,
  };
};
```

- [ ] **Step 4: Run the path unit tests**

Run: `node --test tests/paths.test.js`
Expected: both development and packaged paths pass, including Windows paths containing spaces.

- [ ] **Step 5: Create the package manifest, pin stable dependencies, and lock them**

Add scripts `start`, `test`, `typecheck`, `stage:runtime`, and `make`; make `test` run `node --test`, and make `make` run typecheck, tests, runtime staging, then `electron-forge make`. Add Electron, Electron Forge CLI, Squirrel maker, `electron-squirrel-startup`, TypeScript and Node type declarations. Resolve the current stable release versions and pin them exactly with the commands below.

```json
{
  "name": "solo-empire-video-postproduction-desktop",
  "productName": "Solo Empire Video Studio",
  "version": "0.1.0",
  "private": true,
  "type": "commonjs",
  "author": "Solo Empire",
  "description": "Windows desktop shell for the local-first video post-production studio.",
  "main": "src/main.js",
  "scripts": {
    "start": "electron-forge start",
    "test": "node --test",
    "typecheck": "tsc --noEmit",
    "stage:runtime": "node scripts/stage-runtime.js",
    "make": "npm run typecheck && npm test && npm run stage:runtime && electron-forge make"
  }
}
```

```powershell
npm install --save-exact --save-dev electron @electron-forge/cli @electron-forge/maker-squirrel typescript @types/node
npm install --save-exact electron-squirrel-startup
npm ci
```

- Create tsconfig.json with allowJs, checkJs, strict, noEmit, module CommonJS,
  moduleResolution Node, target ES2022, and types ["node"]; include only
  src/**/*.js, src/**/*.d.ts, scripts/**/*.js, tests/**/*.js, and forge.config.js, and exclude
  dist and node_modules. Add JSDoc parameter/return types to the initial path
  module so the foundation typecheck is clean.

- [ ] **Step 6: Verify package install, checks, and commit the scoped foundation**

Run: `npm test` and `npm run typecheck`
Expected: lockfile installs reproducibly, path tests pass, and no Electron runtime code is launched.

```powershell
git add -- apps/video-postproduction-desktop/package.json apps/video-postproduction-desktop/package-lock.json apps/video-postproduction-desktop/tsconfig.json apps/video-postproduction-desktop/src/runtime/constants.js apps/video-postproduction-desktop/src/runtime/types.d.ts apps/video-postproduction-desktop/src/runtime/paths.js apps/video-postproduction-desktop/tests/paths.test.js
git commit -m "feat: scaffold video studio desktop package"
```

### Task 2: Safe Docker CLI discovery and execution

**Files:**
- Create: `apps/video-postproduction-desktop/src/runtime/docker-cli.js`
- Create: `apps/video-postproduction-desktop/src/runtime/diagnostics.js`
- Test: `apps/video-postproduction-desktop/tests/docker-cli.test.js`
- Test: `apps/video-postproduction-desktop/tests/diagnostics.test.js`

**Interfaces:**
- Consumes: `RuntimePaths` and fixed arguments from trusted main-process code only.
- Produces: `DockerCli.run(args, options)`, `DockerCli.preflight()`, `findDockerExecutable(...)`, and `sanitizeDiagnostic(text)`.

- [ ] **Step 1: Add tests for executable discovery, safe spawn options, and bounded output**

```js
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");
const { PassThrough } = require("node:stream");
const { findDockerExecutable, DockerCli } = require("../src/runtime/docker-cli");

function fakeSuccessfulChild() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  queueMicrotask(() => {
    child.stdout.end();
    child.stderr.end();
    child.emit("close", 0, null);
  });
  return child;
}

test("test_path_with_spaces_remains_one_argument", () => {
  const found = findDockerExecutable({
    platform: "win32",
    pathEntries: ["C:\\Program Files\\Docker\\Docker\\resources\\bin"],
    exists: (candidate) => candidate === "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
  });
  assert.equal(found, "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe");
});

test("runs the executable with shell disabled and hides the console window", async () => {
  let observed;
  const cli = new DockerCli({
    executable: "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
    spawnImpl: (exe, args, options) => {
      observed = { exe, args, options };
      return fakeSuccessfulChild();
    },
  });

  await cli.run(["compose", "version"]);
  assert.equal(observed.exe.includes("Program Files"), true);
  assert.deepEqual(observed.args, ["compose", "version"]);
  assert.equal(observed.options.shell, false);
  assert.equal(observed.options.windowsHide, true);
});
```

- [ ] **Step 2: Run the tests and verify they fail before implementation**

Run: `node --test tests/docker-cli.test.js tests/diagnostics.test.js`
Expected: FAIL because the runtime modules and test child-process fixture do not exist.

- [ ] **Step 3: Implement fixed executable discovery and bounded process output**

Search each PATH directory for `docker.exe` on Windows; also check the Docker Desktop CLI under `ProgramFiles` when present. Inject `exists` and `spawnImpl` for tests. Use `spawn(executable, args, { shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })`; retain only the last 64 KiB of stdout/stderr, enforce caller-provided timeout, and return numeric exit code. Never pass renderer input to `run`.

```js
exports.findDockerExecutable = function findDockerExecutable({ platform, pathEntries, programFiles, exists }) {
  const suffix = platform === "win32" ? "docker.exe" : "docker";
  const candidates = pathEntries.map((entry) => path.join(entry, suffix));
  if (platform === "win32" && programFiles) {
    candidates.push(path.join(programFiles, "Docker", "Docker", "resources", "bin", "docker.exe"));
  }
  return candidates.find((candidate) => exists(candidate)) ?? null;
};
```

- [ ] **Step 4: Implement preflight and diagnostic sanitization**

`preflight()` runs exactly `docker compose version` and `docker info --format {{.OSType}}`; parse and return Compose semver, but production requires only Compose v2 and a Linux engine. The isolated smoke override additionally requires Compose >=2.24.4; if older, report that acceptance step as skipped rather than attempting config/start. Non-zero exit or engine output other than `linux` returns a typed failure. Redact values for environment names containing `PASSWORD`, `TOKEN`, `SECRET`, or `KEY`; do not log the full environment or raw media/API payloads.

Add tests named `test_missing_cli_is_typed_error` and `test_engine_must_report_linux`.
The first injects a missing executable and asserts a stable code/message with no spawn;
the second injects `windows` and non-zero daemon results and asserts preflight fails.
Keep `test_path_with_spaces_remains_one_argument` as the named path-discovery test.

```js
test("redacts credential-shaped values before diagnostics are returned", () => {
  const { sanitizeDiagnostic } = require("../src/runtime/diagnostics");
  const safe = sanitizeDiagnostic("POSTGRES_PASSWORD=local-secret API_TOKEN=abc123");
  assert.equal(safe.includes("local-secret"), false);
  assert.equal(safe.includes("abc123"), false);
});
```

- [ ] **Step 5: Run the tests and commit the Docker boundary**

Run: `node --test tests/docker-cli.test.js tests/diagnostics.test.js`
Expected: missing CLI, daemon-unavailable, Windows path-with-spaces, timeout, capped output, non-Linux engine and redaction tests all pass.

```powershell
git add -- apps/video-postproduction-desktop/src/runtime/docker-cli.js apps/video-postproduction-desktop/src/runtime/diagnostics.js apps/video-postproduction-desktop/tests/docker-cli.test.js apps/video-postproduction-desktop/tests/diagnostics.test.js
git commit -m "feat: add safe docker cli boundary"
```

### Task 3: Compose snapshot and local service identity probes

**Files:**
- Create: `apps/video-postproduction-desktop/src/runtime/compose.js`
- Create: `apps/video-postproduction-desktop/src/runtime/health.js`
- Test: `apps/video-postproduction-desktop/tests/compose.test.js`
- Test: `apps/video-postproduction-desktop/tests/health.test.js`

**Interfaces:**
- Consumes: `DockerCli`, `RuntimePaths`, `PROJECT_NAME`, `SERVICE_NAMES`, `API_ORIGIN`, and `WEB_ORIGIN`.
- Produces: `composeArgs`, `parseComposePs`, `inspectComposeProject`, and `probeRuntime`.

- [ ] **Step 1: Write Compose argv and NDJSON parser tests**

```js
const assert = require("node:assert/strict");
const test = require("node:test");
const { composeArgs, parseComposePs } = require("../src/runtime/compose");

test("pins project name and paths before the Compose subcommand", () => {
  const paths = {
    bundleRoot: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime",
    composeFile: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime\\services\\video-postproduction\\docker-compose.yml",
    composeProjectDirectory: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime\\services\\video-postproduction",
  };
  assert.deepEqual(composeArgs(paths, "video-postproduction", ["ps", "--all", "--format", "json"]), [
    "compose", "--project-name", "video-postproduction", "--file", paths.composeFile,
    "--project-directory", paths.composeProjectDirectory, "ps", "--all", "--format", "json",
  ]);
});

test("adds only the fixed test override when a smoke profile is explicitly selected", () => {
  const paths = {
    composeFile: "C:\\bundle\\services\\video-postproduction\\docker-compose.yml",
    composeFiles: [
      "C:\\bundle\\services\\video-postproduction\\docker-compose.yml",
      "C:\\repo\\tests\\fixtures\\docker-compose.native-smoke.override.yml",
    ],
    composeProjectDirectory: "C:\\bundle\\services\\video-postproduction",
  };
  const args = composeArgs(paths, "video-postproduction-native-smoke-test", ["ps", "--all", "--format", "json"]);
  assert.equal(args.filter((arg) => arg === "--file").length, 2);
  assert.equal(args.includes("video-postproduction"), false);
});

test("parses Compose JSON lines and classifies all five services", () => {
  const snapshot = parseComposePs([
    '{"Service":"api","State":"running","Health":"healthy"}',
    '{"Service":"web","State":"running","Health":"healthy"}',
  ].join("\n"));
  assert.equal(snapshot.api, "healthy");
  assert.equal(snapshot.web, "healthy");
  assert.equal(snapshot.postgres, "unknown");
});
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `node --test tests/compose.test.js tests/health.test.js`
Expected: FAIL because `compose.js` and `health.js` have not been created.

- [ ] **Step 3: Implement fixed Compose commands and validated status parsing**

```js
const { PROJECT_NAME } = require("./constants");

exports.composeArgs = function composeArgs(paths, projectName, subcommandArgs) {
  if (projectName !== PROJECT_NAME && projectName !== "video-postproduction-native-smoke-test") {
    throw new Error("invalid Compose project name");
  }
  const composeFiles = paths.composeFiles ?? [paths.composeFile];
  return [
    "compose", "--project-name", projectName,
    ...composeFiles.flatMap((file) => ["--file", file]),
    "--project-directory", paths.composeProjectDirectory, ...subcommandArgs,
  ];
};
```

Parse each non-empty stdout line as a JSON object (Compose v2 emits NDJSON). Accept only the five declared service names and map `State`/`Health` to the fixed status union; reject malformed JSON and unknown services rather than interpreting them as healthy. Add `inspectComposeProject()` to call `ps --all --format json`; an empty result means no containers. Do not enumerate or mutate volumes as part of inspection.

- [ ] **Step 4: Implement API/web identity probe with an abort timeout**

Use injected `fetchImpl` and an `AbortController` per request. API is `healthy` only for HTTP success plus JSON `{ "service": "video-postproduction-api", "status": "ok" }`; web is `healthy` only for HTTP success plus the expected page title `Post-production studio`. Connection refusal/timeout is `unavailable`; any response from the wrong service/page is `foreign`.

```js
test("test_wrong_api_identity_is_foreign", async () => {
  const result = await probeRuntime({
    fetchImpl: async (url) => url.endsWith("/healthz")
      ? new Response(JSON.stringify({ service: "unrelated-api", status: "ok" }), { status: 200 })
      : new Response("<title>Post-production studio</title>", { status: 200 }),
    apiOrigin: "http://127.0.0.1:8000",
    webOrigin: "http://127.0.0.1:3400",
    timeoutMs: 500,
  });
  assert.equal(result.api, "foreign");
});
```

Add the paired named test `test_wrong_web_title_is_foreign`, returning the correct
API health object but an unrelated page title. Also cover each parser branch: empty
stdout, stopped/exited services, unhealthy health state, malformed JSON, and an
undeclared service name.

- [ ] **Step 5: Run the focused tests and commit the Compose/probe layer**

Run: `node --test tests/compose.test.js tests/health.test.js`
Expected: NDJSON, empty output, malformed rows, stopped services, wrong identity, timeout, port conflict and exact Compose argv cases pass.

```powershell
git add -- apps/video-postproduction-desktop/src/runtime/compose.js apps/video-postproduction-desktop/src/runtime/health.js apps/video-postproduction-desktop/tests/compose.test.js apps/video-postproduction-desktop/tests/health.test.js
git commit -m "feat: inspect video post-production compose runtime"
```

### Task 4: Runtime attach/start/recover/stop state machine

**Files:**
- Create: `apps/video-postproduction-desktop/src/runtime/manager.js`
- Test: `apps/video-postproduction-desktop/tests/manager.test.js`
- Create: `apps/video-postproduction-desktop/tests/helpers/fake-runtime.js`

**Interfaces:**
- Consumes: `DockerCli`, `inspectComposeProject`, `probeRuntime`, injected `sleep`/clock, and a main-process runtime config. Production config is fixed in `main.js`; only the test smoke script may use the separately fixed smoke config.
- Produces: `RuntimeManager.inspect/start/recover/stop`, returning only the `RuntimeStatus` shape defined above.

- [ ] **Step 1: Write tests for attach/no-mutation, stopped stack, first start, partial stack, and port conflict**

```js
const assert = require("node:assert/strict");
const test = require("node:test");
const { RuntimeManager } = require("../src/runtime/manager");
const { createFakeRuntime } = require("./helpers/fake-runtime");

test("attaches to an already healthy runtime without mutating Compose", async () => {
  const fake = createFakeRuntime({ probeResults: [{ api: "healthy", web: "healthy" }] });
  const manager = new RuntimeManager(fake.dependencies);

  const status = await manager.start();

  assert.equal(status.phase, "ready");
  assert.equal(status.action, "attached");
  assert.deepEqual(fake.composeWrites, []);
});

test("partial runtime requires an explicit recovery action", async () => {
  const fake = createFakeRuntime({
    probeResults: [{ api: "healthy", web: "unavailable" }],
    snapshot: { api: "healthy", web: "stopped", postgres: "healthy", redis: "healthy", worker: "healthy" },
  });
  const status = await new RuntimeManager(fake.dependencies).start();
  assert.equal(status.phase, "needs-action");
  assert.equal(status.code, "partial-runtime");
  assert.deepEqual(fake.composeWrites, []);
});
```

Implement `createFakeRuntime({ snapshot, probeResults, preflightResult, composeResults })`
in `tests/helpers/fake-runtime.js`. It returns `{ dependencies, composeWrites, publishedStatuses }`;
`dependencies` contains injected no-network functions, a clock advanced only by fake
`sleep`, a default empty five-service snapshot, and a default successful Compose result.
Each probe/Compose result is consumed in order and then the last result is reused. Tests
must import this helper explicitly; do not depend on globals or undeclared fixtures.

Before implementation, add focused cases named `test_start_builds_when_no_containers`;
`test_start_reuses_stopped_services_without_build`; `test_foreign_port_is_never_opened`;
`test_single_healthy_endpoint_requires_action`; and `test_health_timeout_preserves_stack`.
Assert the exact Compose argv suffix in every mutating test. `test_recover_never_removes_volumes`
must call `recover()` explicitly and verify only `up --detach --build`; the retry case
must call `start()` again after a failed/timeout status, not a nonexistent `retry()` method.

- [ ] **Step 2: Run tests to verify they fail because the manager is missing**

Run: `node --test tests/manager.test.js`
Expected: FAIL because `src/runtime/manager.js` does not exist.

- [ ] **Step 3: Implement the lifecycle transitions**

`start()` runs preflight, probes, then inspects Compose. If both endpoints are healthy,
return `ready/attached` without any Compose write. If either endpoint is `foreign`,
return `failed/port-conflict`. If exactly one endpoint is healthy and the other is
unavailable, return `needs-action/partial-runtime` without mutation so startup cannot
collide with a service outside the project. If both are unavailable and there are no
containers, run `up --detach --build`; if all five services are stopped, run
`up --detach` to reuse them. Any other incomplete/unhealthy project returns
`needs-action/partial-runtime` without mutation. Poll health with injected `sleep`
until success or a 15-minute first-start deadline. `recover()` is only called after
an explicit confirmation in the UI and runs `up --detach --build`. `stop()` runs
`stop web worker api redis postgres` only. A known Docker bind failure for ports
8000/3400 maps to `port-conflict` with a Thai safe message; other failures map to
`compose-failed` and never expose raw output.

```js
const START_NEW_ARGS = ["up", "--detach", "--build"];
const START_EXISTING_ARGS = ["up", "--detach"];
const STOP_ARGS = ["stop", "web", "worker", "api", "redis", "postgres"];

// Keep status progress serializable and publish it before each long operation.
async function publish(phase, code, message, services) {
  const status = { phase, code, message, services, action: null };
  await publishStatus(status);
  return status;
}
```

- [ ] **Step 4: Pin failure safety and explicit recovery behavior**

Add tests `test_failed_start_never_runs_cleanup`, `test_recover_never_removes_volumes`, and `test_stop_uses_compose_stop_only`. Have the fake Docker record all argument arrays; assert no array contains `down`, `-v`, `volume rm`, `kill`, or `restart`. Confirm `recover()` is not reachable through `start()` for a partial project.

- [ ] **Step 5: Run manager tests and commit the lifecycle**

Run: `node --test tests/manager.test.js`
Expected: healthy attach, first start, all-stopped restart, partial confirmation, port conflict, timeout followed by a second explicit `start()` call, stop, and no-cleanup tests pass.

```powershell
git add -- apps/video-postproduction-desktop/src/runtime/manager.js apps/video-postproduction-desktop/tests/manager.test.js apps/video-postproduction-desktop/tests/helpers/fake-runtime.js
git commit -m "feat: manage desktop video runtime lifecycle"
```

### Task 5: Secure Electron windows, launcher bridge, and native menu

**Files:**
- Create: `apps/video-postproduction-desktop/src/security/window-policy.js`
- Create: `apps/video-postproduction-desktop/src/security/window-options.js`
- Create: `apps/video-postproduction-desktop/src/electron/lifecycle.js`
- Create: `apps/video-postproduction-desktop/src/protocol/launcher-protocol.js`
- Create: `apps/video-postproduction-desktop/src/security/launcher-bridge.js`
- Create: `apps/video-postproduction-desktop/src/preload.js`
- Create: `apps/video-postproduction-desktop/src/renderer.d.ts`
- Create: `apps/video-postproduction-desktop/src/main.js`
- Create: `apps/video-postproduction-desktop/src/launcher/index.html`
- Create: `apps/video-postproduction-desktop/src/launcher/launcher.js`
- Create: `apps/video-postproduction-desktop/src/launcher/launcher.css`
- Test: `apps/video-postproduction-desktop/tests/window-policy.test.js`
- Test: `apps/video-postproduction-desktop/tests/protocol.test.js`
- Test: `apps/video-postproduction-desktop/tests/ipc-policy.test.js`
- Test: `apps/video-postproduction-desktop/tests/electron-lifecycle.test.js`

**Interfaces:**
- Consumes: `RuntimeManager` and `RuntimeStatus`.
- Produces: `isAllowedNavigation(url, windowKind)`, `getApprovedExternalUrl(url)`, `isTrustedLauncherSender(senderFrame)`, and `window.videoStudio` with only the four methods locked above.

- [ ] **Step 1: Write security/window/lifecycle tests before creating Electron windows**

```js
test("test_external_navigation_is_blocked", () => {
  const { isAllowedNavigation } = require("../src/security/window-policy");
  assert.equal(isAllowedNavigation("solo-video://launcher/index.html", "launcher"), true);
  assert.equal(isAllowedNavigation("http://127.0.0.1:3400/projects", "studio"), true);
  assert.equal(isAllowedNavigation("http://localhost:3400/projects", "studio"), false);
  assert.equal(isAllowedNavigation("https://example.com", "studio"), false);
  assert.equal(isAllowedNavigation("http://127.0.0.1:3401", "studio"), false);
});

test("test_untrusted_sender_is_rejected", () => {
  const { isTrustedLauncherSender } = require("../src/security/window-policy");
  assert.equal(isTrustedLauncherSender({ origin: "solo-video://launcher", isMainFrame: true }), true);
  assert.equal(isTrustedLauncherSender({ origin: "solo-video://launcher", isMainFrame: false }), false);
  assert.equal(isTrustedLauncherSender({ origin: "http://127.0.0.1:3400", isMainFrame: true }), false);
});
```

- [ ] **Step 2: Run policy tests and verify they fail**

Run: `node --test tests/window-policy.test.js tests/protocol.test.js tests/ipc-policy.test.js`
Expected: FAIL because security and protocol modules are absent.

In `ipc-policy.test.js`, add `test_preload_exposes_only_launcher_methods`. Import the
pure `createLauncherBridge(fakeIpcRenderer)` from `src/security/launcher-bridge.js`
and assert its own keys are exactly `getStatus`, `start`, `recover`, and `onStatus`;
invoke each stub to verify fixed IPC channel names, and verify unsubscription removes
only its paired status listener.

In `electron-lifecycle.test.js`, test no single-instance lock means quit/no window;
second-instance re-focuses the launcher; Squirrel startup exits before window creation;
closing or quitting never calls `manager.stop()`; Stop Engine decline never stops, and
explicit confirmation calls `stop()` exactly once. `window-options.test` coverage may
live in `window-policy.test.js`: launcher preferences include the preload and secure
flags, while Studio preferences contain no `preload` property at all.

- [ ] **Step 3: Implement the protocol, window policy, and narrow preload bridge**

Register `solo-video` as a standard, secure, fetch-capable scheme before `app.whenReady()`. In development resolve launcher assets from `src/launcher`; when packaged resolve them from `process.resourcesPath/launcher`. Serve only `index.html`, `launcher.js`, and `launcher.css`; reject encoded traversal, absolute paths, query/path ambiguity, and unknown filenames. Set launcher CSP with same-origin scripts/styles, no remote sources, `object-src 'none'`, and `frame-src 'none'`. Preload exposes `getStatus`, `start`, `recover`, and `onStatus` only; it never exposes `ipcRenderer`, `shell`, or Docker methods. `renderer.d.ts` imports the canonical `RuntimeStatus` and declares precisely those four bridge methods. IPC authorization also matches the registered launcher's `webContents.id`, exact origin, and main-frame bit; a Studio window or iframe gets no handler access.

```js
const { contextBridge, ipcRenderer } = require("electron");
const { createLauncherBridge } = require("./security/launcher-bridge");

contextBridge.exposeInMainWorld("videoStudio", createLauncherBridge(ipcRenderer));
```

- [ ] **Step 4: Implement separate launcher and Studio windows**

Launcher `BrowserWindow` uses `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`, and `webSecurity: true`. Once health is ready, open a second Studio `BrowserWindow` at `http://127.0.0.1:3400` with the same safe preferences but **no preload**. Add CSP only to responses from that exact Studio origin: allow same-origin assets, inline script/style required by the existing Next.js hydration, and API/media/connect traffic only to `127.0.0.1:8000` and `localhost:8000`; deny objects and frames, and do not allow remote sources or `unsafe-eval`. Preserve any stricter server CSP and verify the existing editor loads under the policy. `isAllowedNavigation` permits only the launcher scheme in the launcher and the exact loopback Studio origin in Studio. Deny redirects and navigation elsewhere. For a user-clicked external link, prevent in-app navigation and call `shell.openExternal` only for the exact Docker Desktop installation help URL `https://docs.docker.com/desktop/setup/install/windows-install/`; all other external links are denied. Build a Thai progress/error UI from status codes, not raw CLI output. The Studio `close` event must never call `RuntimeManager.stop()`.

- [ ] **Step 5: Add single-instance handling and a confirmation-gated Stop Engine menu**

Use the tested helpers in `src/electron/lifecycle.js` to call `app.requestSingleInstanceLock()`, focus/reuse the existing launcher for `second-instance`, and exit Squirrel first-run/update/uninstall events before creating windows. The native menu action `หยุด Engine` first shows a warning that current processing can be interrupted; only the explicit confirmation calls `manager.stop()`. The app close/quit paths do not call `stop`, `down`, or `restart`.

- [ ] **Step 6: Run unit tests and commit the shell/security boundary**

Run: `node --test tests/window-policy.test.js tests/protocol.test.js tests/ipc-policy.test.js tests/electron-lifecycle.test.js`; then `npm run typecheck`.
Expected: origin allowlist, external navigation denial, path traversal rejection, sender validation, exact preload API, no-preload Studio window, single-instance behavior, and confirmation/safe-shutdown tests pass.

Name the protocol test `test_launcher_protocol_rejects_path_traversal` and test both
plain/percent-encoded `..`, absolute paths, and non-allowlisted assets. Test the
allowlisted Docker help URL separately from in-app navigation; no arbitrary HTTPS
URL may reach `shell.openExternal`.

```powershell
git add -- apps/video-postproduction-desktop/src/security/window-policy.js apps/video-postproduction-desktop/src/security/window-options.js apps/video-postproduction-desktop/src/security/launcher-bridge.js apps/video-postproduction-desktop/src/protocol/launcher-protocol.js apps/video-postproduction-desktop/src/electron/lifecycle.js apps/video-postproduction-desktop/src/preload.js apps/video-postproduction-desktop/src/renderer.d.ts apps/video-postproduction-desktop/src/main.js apps/video-postproduction-desktop/src/launcher/index.html apps/video-postproduction-desktop/src/launcher/launcher.js apps/video-postproduction-desktop/src/launcher/launcher.css apps/video-postproduction-desktop/tests/window-policy.test.js apps/video-postproduction-desktop/tests/protocol.test.js apps/video-postproduction-desktop/tests/ipc-policy.test.js apps/video-postproduction-desktop/tests/electron-lifecycle.test.js
git commit -m "feat: add secure video studio desktop shell"
```

### Task 6: Reproducible runtime bundle

**Files:**
- Create: `apps/video-postproduction-desktop/scripts/runtime-bundle-manifest.js`
- Create: `apps/video-postproduction-desktop/scripts/stage-runtime.js`
- Modify: `apps/video-postproduction-desktop/src/runtime/paths.js`
- Test: `apps/video-postproduction-desktop/tests/runtime-bundle.test.js`
- Create: `apps/video-postproduction-desktop/tests/helpers/runtime-fixture.js`
- Test: `apps/video-postproduction-desktop/tests/fixtures/docker-compose.native-smoke.override.yml`

**Interfaces:**
- Consumes: tracked Compose/Dockerfile/API/web source paths and `resolveRuntimePaths`.
- Produces: `dist/video-runtime/` with the exact Compose-relative source layout; development and packaged paths resolve to that root. Interface: `stageRuntime({ repoRoot, packageRoot })` derives the only permitted output, `packageRoot/dist/video-runtime`, and returns the generated manifest/hash.

- [ ] **Step 1: Write manifest inclusion/exclusion tests**

```js
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { stageRuntime } = require("../scripts/stage-runtime");
const { makeRuntimeFixture } = require("./helpers/runtime-fixture");

test("stages only required runtime source and excludes machine/user state", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture({
    includeSecret: true, includeNodeModules: true, includeNextBuild: true,
  });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(packageRoot, "dist", "video-runtime");
  await stageRuntime({ repoRoot: root, packageRoot });

  assert.equal(fs.existsSync(path.join(output, "services", "video-postproduction", "docker-compose.yml")), true);
  assert.equal(fs.existsSync(path.join(output, "services", "video-postproduction-api", "video_postproduction", "main.py")), true);
  assert.equal(fs.existsSync(path.join(output, "apps", "video-postproduction-web", "package-lock.json")), true);
  assert.equal(fs.existsSync(path.join(output, "apps", "video-postproduction-web", "lib", "api.test.ts")), false);
  assert.equal(fs.existsSync(path.join(output, "services", "video-postproduction-api", ".env")), false);
  assert.equal(fs.existsSync(path.join(output, "apps", "video-postproduction-web", "node_modules")), false);
  assert.equal(fs.existsSync(path.join(output, "apps", "video-postproduction-web", ".next")), false);
});
```

Implement `makeRuntimeFixture(options)` in `tests/helpers/runtime-fixture.js`. It creates
a unique temporary repository root with every required allowlisted source path and
returns `{ root, packageRoot }`. When enabled, it also adds an API `.env`, web
`node_modules/`, and web `.next/` tree. The test always removes only that exact
temporary root through `t.after`.

- [ ] **Step 2: Run the test and verify it fails**

Run: `node --test tests/runtime-bundle.test.js`
Expected: FAIL because the manifest/stager do not exist.

- [ ] **Step 3: Add an explicit source allowlist and safe staging implementation**

Include exactly the Compose file and `Dockerfile.web`, API `Dockerfile`/`pyproject.toml`/`alembic.ini`/migrations/application package, and the web app's `package.json`, lockfile, Next/PostCSS/TypeScript config, `app/`, `components/`, `lib/api.ts`, plus `public/` only if present. Do not copy `lib/` wholesale because it contains `api.test.ts`. Reject symbolic links and any path resolving outside repository root. Exclude `.env*`, credentials, Git metadata, user data, tests, `node_modules`, `.next`, caches, and build output. Write a deterministic manifest with source-relative paths and SHA-256 hashes (the manifest file itself is not recursively hashed). Before replacing output, assert the resolved destination is exactly `packageRoot/dist/video-runtime`; reject symlinked destination/parent paths and never delete or overwrite any path outside that generated directory.

```js
const REQUIRED_FILES = Object.freeze([
  "services/video-postproduction/docker-compose.yml",
  "services/video-postproduction/Dockerfile.web",
  "services/video-postproduction-api/Dockerfile",
  "services/video-postproduction-api/pyproject.toml",
  "services/video-postproduction-api/alembic.ini",
  "apps/video-postproduction-web/package.json",
  "apps/video-postproduction-web/package-lock.json",
  "apps/video-postproduction-web/next.config.ts",
  "apps/video-postproduction-web/postcss.config.mjs",
  "apps/video-postproduction-web/tsconfig.json",
  "apps/video-postproduction-web/lib/api.ts",
]);
const INCLUDED_DIRECTORIES = Object.freeze([
  "services/video-postproduction-api/migrations",
  "services/video-postproduction-api/video_postproduction",
  "apps/video-postproduction-web/app",
  "apps/video-postproduction-web/components",
  "apps/video-postproduction-web/public",
]);
```

- [ ] **Step 4: Verify the staged Compose config and isolated smoke override**

The fixture override sets the fixed test project name `video-postproduction-native-smoke-test`, API/web host ports `127.0.0.1:18000:8000` / `127.0.0.1:13400:3000`, and isolated image tags `video-postproduction-api:native-smoke-test` / `video-postproduction-web:native-smoke-test`. Override API and worker image to the API test tag, web image to the web test tag, API CORS origin to `http://127.0.0.1:13400`, and both web API-origin settings to `http://127.0.0.1:18000`. Keep the base build definitions so the smoke manager can build only the isolated tags. This prevents retagging or stopping the user's running development images.

Use this Compose override. Compose v2.24.4+ supports the `!override` tag used here to
replace—not append to—the base published ports; when older Compose is detected, skip
the isolated integration smoke and report that prerequisite clearly.

```yaml
services:
  api:
    image: video-postproduction-api:native-smoke-test
    ports: !override
      - "127.0.0.1:18000:8000"
    environment:
      CORS_ALLOWED_ORIGINS: http://127.0.0.1:13400
  worker:
    image: video-postproduction-api:native-smoke-test
  web:
    image: video-postproduction-web:native-smoke-test
    ports: !override
      - "127.0.0.1:13400:3000"
    build:
      args:
        NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN: http://127.0.0.1:18000
    environment:
      NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN: http://127.0.0.1:18000
```

Run from the repository root in PowerShell:

```powershell
$packageRoot = (Resolve-Path "apps/video-postproduction-desktop").Path
$runtimeRoot = Join-Path $packageRoot "dist\video-runtime"
$composeFile = Join-Path $runtimeRoot "services\video-postproduction\docker-compose.yml"
$overrideFile = Join-Path $packageRoot "tests\fixtures\docker-compose.native-smoke.override.yml"
$projectDirectory = Join-Path $runtimeRoot "services\video-postproduction"
$composeVersion = (docker compose version --short).TrimStart("v")
if ($LASTEXITCODE -ne 0) { throw "Could not read Docker Compose version." }
if ([version]$composeVersion -lt [version]"2.24.4") {
  Write-Warning "Skipped isolated override validation: Docker Compose 2.24.4 or later is required for !override."
} else {
  docker compose --project-name video-postproduction-native-smoke-test --file $composeFile --file $overrideFile --project-directory $projectDirectory config --quiet
  if ($LASTEXITCODE -ne 0) { throw "Isolated Compose configuration is invalid." }
}
```

This validates Compose only and starts no containers. If Compose is older than 2.24.4,
record the isolated override check as skipped; the production app still supports
Compose v2 and the installed version check must not block its normal preflight.

- [ ] **Step 5: Run bundle tests and commit the staging boundary**

Run: `node --test tests/runtime-bundle.test.js`; then `npm run stage:runtime` and the exact Compose config command from Step 4.
Expected: clean runtime tree, required build paths present, forbidden files absent,
config valid when Compose >=2.24.4 (otherwise the optional isolated config check is
reported skipped), and `dist/` remains ignored.

```powershell
git add -- apps/video-postproduction-desktop/scripts/runtime-bundle-manifest.js apps/video-postproduction-desktop/scripts/stage-runtime.js apps/video-postproduction-desktop/src/runtime/paths.js apps/video-postproduction-desktop/tests/runtime-bundle.test.js apps/video-postproduction-desktop/tests/helpers/runtime-fixture.js apps/video-postproduction-desktop/tests/fixtures/docker-compose.native-smoke.override.yml
git commit -m "build: stage minimal video runtime for desktop"
```

### Task 7: Windows installer and repository handoff docs

**Files:**
- Create: `apps/video-postproduction-desktop/forge.config.js`
- Modify: `apps/video-postproduction-desktop/package.json`
- Modify: `apps/video-postproduction-desktop/package-lock.json`
- Modify: `apps/README.md`
- Modify: `services/video-postproduction/README.md`
- Modify: `infra/tasks/products.yml`
- Test: `apps/video-postproduction-desktop/tests/installer-contract.test.js`
- Create: `apps/video-postproduction-desktop/scripts/verify-artifacts.js`
- Test: `apps/video-postproduction-desktop/tests/installer-artifacts.test.js`

**Interfaces:**
- Consumes: tested Electron shell and `dist/video-runtime` from Tasks 1–6.
- Produces: per-user x64 `Solo Empire Video Studio Setup.exe`, package-local commands, and documented prerequisite/data-retention behavior.

- [ ] **Step 1: Write installer contract tests**

```js
test("installer config targets only Windows Squirrel and packages the runtime resource", () => {
  const path = require("node:path");
  const config = require("../forge.config");
  const packageInfo = require("../package.json");
  assert.deepEqual(config.makers.map((maker) => maker.name), ["@electron-forge/maker-squirrel"]);
  assert.equal(config.outDir, path.resolve(__dirname, "..", "dist", "forge-out"));
  assert.equal(config.makers[0].config.name, "SoloEmpireVideoStudio");
  assert.deepEqual(config.packagerConfig.extraResource, ["dist/video-runtime", "src/launcher"]);
  assert.equal(config.packagerConfig.asar, true);
  assert.equal(packageInfo.author, "Solo Empire");
  assert.equal(packageInfo.productName, "Solo Empire Video Studio");
  assert.equal("certificateFile" in config.makers[0].config, false);
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `node --test tests/installer-contract.test.js`
Expected: FAIL because `forge.config.js` does not exist.

In the same test step, exercise `verifyArtifacts(packageRoot)` against a temporary
package root containing `dist/forge-out/make/squirrel.windows/x64` with one Setup.exe,
one `.nupkg`, and one `RELEASES`; assert missing/duplicate artifact types fail and a
symlinked artifact directory resolving outside `dist/forge-out` is rejected.

- [ ] **Step 3: Configure Squirrel.Windows per-user packaging**

Set `forge.config.js` to package `dist/video-runtime` and launcher assets as `extraResource`, keep app source in ASAR, and set Forge `outDir` to `path.resolve(__dirname, "dist", "forge-out")`. Configure the sole Squirrel maker with package identifier `SoloEmpireVideoStudio`, author `Solo Empire`, and the package description; set the Squirrel app user-model identity in main process. Do not set signing credentials, publishers, or machine-wide install options. Target only Windows x64 through the explicit Forge CLI flags in Step 5.

- [ ] **Step 4: Add developer Task wrappers and Thai-first usage docs**

Add `verify:artifacts` to the package scripts and implement `verifyArtifacts(packageRoot)` as a pure function with a CLI entry point. The CLI passes its own package root; the function derives the expected x64 Squirrel directory, stays beneath `dist/forge-out`, rejects symlinks, and requires exactly one file of each artifact type. Do not accept an arbitrary search root from command-line input.

Add `video-postproduction:desktop:test`, `video-postproduction:desktop:typecheck`, and `video-postproduction:desktop:make` in `infra/tasks/products.yml`, each with `dir: apps/video-postproduction-desktop` and the corresponding npm command. That Taskfile currently contains unrelated uncommitted edits: preserve them and stage only the three new task hunks. Update `apps/README.md` and `services/video-postproduction/README.md` with Docker Desktop/Linux-engine prerequisite, Start Menu install, first-build internet/disk note, unsigned SmartScreen warning, existing Compose/volume reuse, close-window behavior, explicit Stop Engine warning, and uninstall retention. Do not document AI highlight as delivered.

- [ ] **Step 5: Run package checks and generate the unsigned local installer**

Run from the desktop package: `npm ci`, `npm run make -- --platform=win32 --arch=x64`, then `npm run verify:artifacts`. The make script already runs typecheck, tests, runtime staging, and Forge.
Expected: `scripts/verify-artifacts.js` resolves the configured `dist/forge-out/make/squirrel.windows/x64` directory, rejects any resolved path outside `dist/forge-out`, and requires exactly one app `Setup.exe`, one `.nupkg`, and one `RELEASES` file within it. The script prints only the artifact paths and SHA-256 of the Setup.exe.

- [ ] **Step 6: Commit only package/docs/task adapter files**

```powershell
git add -- apps/video-postproduction-desktop/forge.config.js apps/video-postproduction-desktop/package.json apps/video-postproduction-desktop/package-lock.json apps/video-postproduction-desktop/scripts/verify-artifacts.js apps/video-postproduction-desktop/tests/installer-contract.test.js apps/video-postproduction-desktop/tests/installer-artifacts.test.js apps/README.md services/video-postproduction/README.md infra/tasks/products.yml
git commit -m "build: package video studio for Windows"
```

### Task 8: Windows acceptance, isolated start/stop smoke, and final verification

**Files:**
- Modify: `apps/video-postproduction-desktop/tests/fixtures/docker-compose.native-smoke.override.yml`
- Modify: `apps/video-postproduction-desktop/tests/manager.test.js`
- Modify: `apps/video-postproduction-desktop/tests/installer-contract.test.js`
- Create: `apps/video-postproduction-desktop/scripts/smoke-runtime.js`
- Modify: `apps/video-postproduction-desktop/package.json`
- Modify: `apps/README.md`
- Modify: `services/video-postproduction/README.md`

**Interfaces:**
- Consumes: local installer, `RuntimeManager`, isolated Compose override, and current healthy `video-postproduction` project.
- Produces: repeatable test evidence for existing-data attach, separate smoke start/stop, package install/uninstall, and final handoff.

- [ ] **Step 1: Run unit/contract gates before touching a real runtime**

Run: `npm test`, `npm run typecheck`, `npm run stage:runtime`, then use the exact isolated Compose config command in the following block.
Expected: unit/type/runtime staging gates pass before launching Electron. Compose
override validation runs only at version >=2.24.4; otherwise record it as skipped.

```powershell
$packageRoot = (Resolve-Path "apps/video-postproduction-desktop").Path
$runtimeRoot = Join-Path $packageRoot "dist\video-runtime"
$composeFile = Join-Path $runtimeRoot "services\video-postproduction\docker-compose.yml"
$overrideFile = Join-Path $packageRoot "tests\fixtures\docker-compose.native-smoke.override.yml"
$projectDirectory = Join-Path $runtimeRoot "services\video-postproduction"
$composeVersion = (docker compose version --short).TrimStart("v")
if ($LASTEXITCODE -ne 0) { throw "Could not read Docker Compose version." }
if ([version]$composeVersion -lt [version]"2.24.4") {
  Write-Warning "Skipped isolated override validation: Docker Compose 2.24.4 or later is required for !override."
} else {
  docker compose --project-name video-postproduction-native-smoke-test --file $composeFile --file $overrideFile --project-directory $projectDirectory config --quiet
  if ($LASTEXITCODE -ne 0) { throw "Isolated Compose configuration is invalid." }
}
```

- [ ] **Step 2: Test first-start and Stop Engine only on the isolated smoke project**

Add a manual-only `smoke:runtime` script to the package. It first requires Compose
2.24.4 or later and reports a skipped status otherwise. It constructs the same
`RuntimeManager` with the fixed smoke project, override file, and origins; it runs
start → stop → start → stop and fails unless each start reaches both API/web identity
checks. Capture the smoke volume names after the first start and confirm they are
identical after the second start by calling `docker volume ls --filter
label=com.docker.compose.project=video-postproduction-native-smoke-test --format
{{.Name}}` through the injected Docker CLI. Build its Compose paths from the already
staged `dist/video-runtime` (never the repository root), with the override path fixed
under `tests/fixtures`. It must not accept a project name, Compose path, origin, or
command from CLI arguments or environment variables; it must not auto-recover a
partial smoke project or auto-run `down`.

Run it from the desktop package after staging. Then remove only the isolated smoke
containers/network, never the test volumes:

```powershell
$packageRoot = (Resolve-Path "apps/video-postproduction-desktop").Path
$runtimeRoot = Join-Path $packageRoot "dist\video-runtime"
$composeFile = Join-Path $runtimeRoot "services\video-postproduction\docker-compose.yml"
$overrideFile = Join-Path $packageRoot "tests\fixtures\docker-compose.native-smoke.override.yml"
$projectDirectory = Join-Path $runtimeRoot "services\video-postproduction"
$composeVersion = (docker compose version --short).TrimStart("v")
if ($LASTEXITCODE -ne 0) { throw "Could not read Docker Compose version." }
if ([version]$composeVersion -lt [version]"2.24.4") {
  Write-Warning "Skipped isolated runtime smoke and cleanup: Docker Compose 2.24.4 or later is required for !override."
} else {
  npm run smoke:runtime
  if ($LASTEXITCODE -ne 0) { throw "Isolated runtime smoke failed." }
  docker compose --project-name video-postproduction-native-smoke-test --file $composeFile --file $overrideFile --project-directory $projectDirectory down
  if ($LASTEXITCODE -ne 0) { throw "Could not remove isolated smoke containers/network." }
}
```

The smoke script must use only its fixed test project and must never invoke
`down -v`. Never use the production project name for this cleanup.

- [ ] **Step 3: Install and attach to the user's existing healthy stack without mutating it**

From the repository root, record only the five service/state/health fields from this
read-only baseline command; do not copy container labels or raw output into the diary:

```powershell
$composeFile = (Resolve-Path "services/video-postproduction/docker-compose.yml").Path
$projectDirectory = (Resolve-Path "services/video-postproduction").Path
docker compose --project-name video-postproduction --file $composeFile --project-directory $projectDirectory ps --all --format json
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the existing runtime." }
```

Install the unsigned `Setup.exe` in the current Windows user profile and launch it
from Start Menu. Verify the API identity, page title, and existing project data
appear. Close/reopen the app, rerun the exact baseline command, and compare service
states only. Do not select Stop Engine and do not run `up`, `restart`, `down`, or an
image build against this healthy project.

- [ ] **Step 4: Verify prerequisite and failure UX**

Use fake Docker/process fixtures for missing CLI, engine-not-ready, port collision, partial project, malformed Compose output, failed start, timeout and denied navigation. On the Windows UI, confirm messages are Thai, contain no raw secret/stack payload, and provide retry or manual next action without requesting Administrator rights.

- [ ] **Step 5: Test per-user uninstall and persistence boundary**

Uninstall the desktop shell from Windows Settings/Start Menu. Confirm Compose still
reports the pre-existing services and that the original named volumes still exist:

```powershell
$composeFile = (Resolve-Path "services/video-postproduction/docker-compose.yml").Path
$projectDirectory = (Resolve-Path "services/video-postproduction").Path
docker compose --project-name video-postproduction --file $composeFile --project-directory $projectDirectory ps --all --format json
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the existing runtime after uninstall." }
docker volume ls --filter "label=com.docker.compose.project=video-postproduction" --format "{{.Name}}"
```

Do not remove or prune any Docker volume.

- [ ] **Step 6: Run repository verification and inspect the final scope**

Run `task test`, `task typecheck`, `task docs:check`, `task docs:links:check`,
`task subagents:check`, `task test:agent`, the three `video-postproduction:desktop:*`
tasks, and `git diff --check`. Record pre-existing unrelated failures exactly; do
not repair or stage unrelated dirty files. Inspect `git status -sb` and
`git diff --cached --stat` before each commit.

- [ ] **Step 7: Complete delivery without publishing unrelated commits**

Commit any final scoped test/doc fix. Do not push if the active branch contains unrelated commits ahead of upstream; do not deploy because Native V1 has no hosted deployment target. Provide installer path, SHA-256, test results, Docker prerequisite, SmartScreen unsigned warning, data-retention behavior, and any remaining gate.

## Official references for implementation

- Electron security checklist: https://www.electronjs.org/docs/latest/tutorial/security
- Electron context isolation: https://www.electronjs.org/docs/latest/tutorial/context-isolation
- Electron sandbox: https://www.electronjs.org/docs/latest/tutorial/sandbox
- Electron IPC and narrow `contextBridge` APIs: https://www.electronjs.org/docs/latest/tutorial/ipc
- Electron Forge Squirrel.Windows installer: https://www.electronforge.io/config/makers/squirrel.windows
- Docker Compose file merge and `!override` (Compose 2.24.4+): https://docs.docker.com/reference/compose-file/merge/
