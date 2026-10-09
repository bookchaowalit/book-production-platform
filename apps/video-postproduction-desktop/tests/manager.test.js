const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const { RuntimeManager } = require("../src/runtime/manager");
const { runSmokeRuntime } = require("../scripts/smoke-runtime");
const { createFakeRuntime } = require("./helpers/fake-runtime");

const SMOKE_PROJECT_NAME = "video-postproduction-native-smoke-test";
const SMOKE_VOLUME_ARGS = [
  "volume", "ls", "--filter", `label=com.docker.compose.project=${SMOKE_PROJECT_NAME}`,
  "--format", "{{.Name}}",
];
const SMOKE_VOLUME_NAMES = [
  `${SMOKE_PROJECT_NAME}_postgres_data`,
  `${SMOKE_PROJECT_NAME}_redis_data`,
  `${SMOKE_PROJECT_NAME}_video_storage`,
  `${SMOKE_PROJECT_NAME}_whisper_cache`,
];
const INVALID_SMOKE_VOLUME_NAMES = [
  SMOKE_VOLUME_NAMES.slice(0, 3),
  SMOKE_VOLUME_NAMES.map((name) => `${name}-unexpected`),
];

/** @param {{ composeVersion?: string, volumeListings?: string[], probeResults?: ProbeResult[] }} [options] */
function createSmokeHarness({
  composeVersion = "v2.24.4",
  volumeListings = [SMOKE_VOLUME_NAMES.join("\n"), SMOKE_VOLUME_NAMES.join("\n")],
  probeResults = [unavailable, healthy, unavailable, healthy],
} = {}) {
  /** @type {string[][]} */
  const dockerCalls = [];
  /** @type {Array<{ apiOrigin: string, webOrigin: string }>} */
  const probeOrigins = [];
  let volumeListIndex = 0;
  let probeIndex = 0;
  /** @type {ConstructorParameters<typeof RuntimeManager>[0] | undefined} */
  let managerOptions;
  let managerFactoryCalls = 0;

  const dockerCli = {
    run: async (/** @type {string[]} */ args) => {
      dockerCalls.push([...args]);
      if (args[0] === "compose" && args[1] === "version") {
        return { exitCode: 0, stdout: composeVersion, stderr: "" };
      }
      if (args[0] === "volume" && args[1] === "ls") {
        const output = volumeListings[Math.min(volumeListIndex, volumeListings.length - 1)] ?? "";
        volumeListIndex += 1;
        return { exitCode: 0, stdout: output, stderr: "" };
      }
      if (args[0] === "compose" && args.includes("ps")) {
        const stdout = ["postgres", "redis", "api", "worker", "web"]
          .map((Service) => JSON.stringify({ Service, State: "exited" }))
          .join("\n");
        return { exitCode: 0, stdout, stderr: "" };
      }
      return { exitCode: 0, stdout: "", stderr: "" };
    },
    /** @returns {Promise<{ composeVersion: string, engine: "linux" }>} */
    preflight: async () => ({ composeVersion: "2.30.0", engine: "linux" }),
  };

  const createRuntimeManager = (/** @type {ConstructorParameters<typeof RuntimeManager>[0]} */ options) => {
    managerFactoryCalls += 1;
    managerOptions = options;
    return new RuntimeManager({
      ...options,
      dockerCli,
      startupTimeoutMs: 0,
      probeRuntime: async (probeOptions) => {
        probeOrigins.push({ apiOrigin: probeOptions.apiOrigin, webOrigin: probeOptions.webOrigin });
        const result = probeResults[Math.min(probeIndex, probeResults.length - 1)] ?? unavailable;
        probeIndex += 1;
        return result;
      },
      now: () => 0,
      sleep: async () => {},
    });
  };

  return {
    dockerCli,
    dockerCalls,
    probeOrigins,
    createRuntimeManager,
    get managerOptions() { return managerOptions; },
    get managerFactoryCalls() { return managerFactoryCalls; },
  };
}

test("manual runtime smoke uses fixed staged paths and smoke project for start-stop-start-stop", async () => {
  const fake = createSmokeHarness();

  const result = await runSmokeRuntime({
    dockerCli: fake.dockerCli,
    createRuntimeManager: fake.createRuntimeManager,
  });
  const managerOptions = fake.managerOptions;

  const packageRoot = path.resolve(__dirname, "..");
  const runtimeRoot = path.join(packageRoot, "dist", "video-runtime");
  const composeFile = path.join(runtimeRoot, "services", "video-postproduction", "docker-compose.yml");
  const overrideFile = path.join(packageRoot, "tests", "fixtures", "docker-compose.native-smoke.override.yml");
  const projectDirectory = path.dirname(composeFile);
  const composePrefix = [
    "compose", "--project-name", SMOKE_PROJECT_NAME,
    "--file", composeFile, "--file", overrideFile,
    "--project-directory", projectDirectory,
  ];

  assert.equal(result.status, "passed");
  if (result.status !== "passed") return;
  assert.deepEqual(result.volumeNames, SMOKE_VOLUME_NAMES);
  assert.deepEqual(fake.dockerCalls[0], ["compose", "version", "--short"]);
  assert.ok(managerOptions);
  assert.equal(fake.managerFactoryCalls, 1);
  assert.equal(managerOptions.projectName, SMOKE_PROJECT_NAME);
  assert.equal(managerOptions.apiOrigin, "http://127.0.0.1:18000");
  assert.equal(managerOptions.webOrigin, "http://127.0.0.1:13400");
  assert.equal(managerOptions.paths.composeFile, composeFile);
  assert.deepEqual(managerOptions.paths.composeFiles, [composeFile, overrideFile]);
  assert.equal(managerOptions.paths.composeProjectDirectory, projectDirectory);
  assert.deepEqual(fake.probeOrigins, Array.from({ length: 4 }, () => ({
    apiOrigin: "http://127.0.0.1:18000",
    webOrigin: "http://127.0.0.1:13400",
  })));

  const composeCalls = fake.dockerCalls.filter((args) => args[0] === "compose" && args.includes("--project-name"));
  for (const args of composeCalls) assert.deepEqual(args.slice(0, composePrefix.length), composePrefix);
  assert.deepEqual(
    composeCalls.map((args) => args[composePrefix.length]).filter((command) => command === "up" || command === "stop"),
    ["up", "stop", "up", "stop"],
  );
  assert.deepEqual(fake.dockerCalls.filter((args) => args[0] === "volume"), [SMOKE_VOLUME_ARGS, SMOKE_VOLUME_ARGS]);
  assert.equal(fake.dockerCalls.some((args) => args.includes("down") || args.includes("-v")), false);
  assert.equal(fake.dockerCalls.some((args) => args[0] === "volume" && args[1] === "rm"), false);
});

test("runtime smoke refuses to stop a smoke project it did not start", async () => {
  const fake = createSmokeHarness();
  let stopCalls = 0;
  const createRuntimeManager = () => ({
    start: async () => ({
      phase: "ready",
      action: "attached",
      services: { api: "healthy", web: "healthy" },
    }),
    stop: async () => {
      stopCalls += 1;
      return { phase: "stopped" };
    },
  });

  await assert.rejects(runSmokeRuntime({
    dockerCli: fake.dockerCli,
    createRuntimeManager,
  }), /did not start this smoke project/);

  assert.equal(stopCalls, 0);
  assert.equal(fake.dockerCalls.some((args) => args[0] === "volume"), false);
});

test("runtime smoke reports skipped and performs no project operations below Compose 2.24.4", async () => {
  const fake = createSmokeHarness({ composeVersion: "2.24.3" });

  const result = await runSmokeRuntime({
    dockerCli: fake.dockerCli,
    createRuntimeManager: fake.createRuntimeManager,
  });

  assert.equal(result.status, "skipped");
  assert.equal(result.composeVersion, "2.24.3");
  assert.equal(fake.managerFactoryCalls, 0);
  assert.deepEqual(fake.dockerCalls, [["compose", "version", "--short"]]);
});

test("runtime smoke does not recover or stop a partial project reported by RuntimeManager", async () => {
  const fake = createSmokeHarness({ probeResults: [{ api: "healthy", web: "unavailable" }] });

  await assert.rejects(runSmokeRuntime({
    dockerCli: fake.dockerCli,
    createRuntimeManager: fake.createRuntimeManager,
  }), /did not verify both API and web identities/);

  const composeCalls = fake.dockerCalls.filter((args) => args[0] === "compose");
  assert.equal(composeCalls.some((args) => args.includes("up") || args.includes("stop") || args.includes("down")), false);
  assert.equal(fake.dockerCalls.some((args) => args[0] === "volume"), false);
});

test("runtime smoke completes its final stop before rejecting changed volume names", async () => {
  const changedNames = SMOKE_VOLUME_NAMES.map((name) => `${name}-changed`).join("\n");
  const fake = createSmokeHarness({ volumeListings: [SMOKE_VOLUME_NAMES.join("\n"), changedNames] });

  await assert.rejects(runSmokeRuntime({
    dockerCli: fake.dockerCli,
    createRuntimeManager: fake.createRuntimeManager,
  }), /smoke project volume set does not match the expected contract/);

  const composeCalls = fake.dockerCalls.filter((args) => args[0] === "compose" && args.includes("--project-name"));
  const prefixLength = composeCalls[0].indexOf("--project-directory") + 2;
  assert.deepEqual(
    composeCalls.map((args) => args[prefixLength]).filter((command) => command === "up" || command === "stop"),
    ["up", "stop", "up", "stop"],
  );
  assert.equal(fake.dockerCalls.some((args) => args.includes("down") || args.includes("-v")), false);
  assert.equal(fake.dockerCalls.some((args) => args[0] === "volume" && args[1] === "rm"), false);
});

test("runtime smoke stops its own project and rejects missing or unexpected volume names", async () => {
  for (const invalidNames of INVALID_SMOKE_VOLUME_NAMES) {
    const fake = createSmokeHarness({ volumeListings: [invalidNames.join("\n")] });

    await assert.rejects(runSmokeRuntime({
      dockerCli: fake.dockerCli,
      createRuntimeManager: fake.createRuntimeManager,
    }), /volume set does not match the expected contract/);

    const composeCalls = fake.dockerCalls.filter((args) => args[0] === "compose" && args.includes("--project-name"));
    const prefixLength = composeCalls[0].indexOf("--project-directory") + 2;
    assert.deepEqual(
      composeCalls.map((args) => args[prefixLength]).filter((command) => command === "up" || command === "stop"),
      ["up", "stop"],
    );
    assert.equal(fake.dockerCalls.filter((args) => args[0] === "volume").length, 1);
  }
});

/** @typedef {"healthy" | "unavailable" | "foreign"} EndpointState */
/** @typedef {{ api: EndpointState, web: EndpointState }} ProbeResult */
/** @typedef {import("../src/runtime/types").ServiceName} ServiceName */
/** @typedef {import("../src/runtime/types").ServiceState} ServiceState */

/** @type {ProbeResult} */
const unavailable = { api: "unavailable", web: "unavailable" };
/** @type {ProbeResult} */
const healthy = { api: "healthy", web: "healthy" };
/** @type {Record<ServiceName, ServiceState>} */
const stoppedServices = {
  postgres: "stopped",
  redis: "stopped",
  api: "stopped",
  worker: "stopped",
  web: "stopped",
};

/** @param {string[][]} calls @param {string[]} suffix */
function assertComposeSuffix(calls, suffix) {
  assert.equal(calls.length > 0, true);
  const lastCall = calls[calls.length - 1];
  assert.ok(lastCall);
  assert.deepEqual(lastCall.slice(-suffix.length), suffix);
}

/** @param {string[][]} calls */
function assertNoCleanup(calls) {
  const forbidden = new Set(["down", "-v", "kill", "restart", "volume"]);
  for (const args of calls) {
    assert.equal(args.some((argument) => forbidden.has(argument)), false, `unsafe command: ${args.join(" ")}`);
  }
}

test("attaches to an already healthy runtime without mutating Compose", async () => {
  const fake = createFakeRuntime({ probeResults: [healthy] });
  const manager = new RuntimeManager(fake.dependencies);

  const status = await manager.start();

  assert.equal(status.phase, "ready");
  assert.equal(status.action, "attached");
  assert.deepEqual(fake.composeWrites, []);
});

test("inspect is read-only and returns the canonical status shape", async () => {
  const fake = createFakeRuntime({ probeResults: [healthy] });
  const status = await new RuntimeManager(fake.dependencies).inspect();

  assert.deepEqual(Object.keys(status).sort(), ["action", "code", "message", "phase", "services"]);
  assert.equal(status.phase, "ready");
  assert.deepEqual(fake.composeWrites, []);
});

test("invalid Compose snapshots return a safe invalid-runtime status", async () => {
  const fake = createFakeRuntime({ probeResults: [unavailable] });
  const manager = new RuntimeManager({
    ...fake.dependencies,
    inspectComposeProject: async () => { throw new Error("unexpected Compose service"); },
  });

  const status = await manager.inspect();

  assert.equal(status.phase, "failed");
  assert.equal(status.code, "invalid-runtime");
  assert.equal(status.message.includes("unexpected Compose service"), false);
});

test("test_start_builds_when_no_containers", async () => {
  const fake = createFakeRuntime({ probeResults: [unavailable, healthy] });
  const manager = new RuntimeManager({
    ...fake.dependencies,
    startupTimeoutMs: 100,
    pollIntervalMs: 10,
  });

  const status = await manager.start();

  assert.equal(status.phase, "ready");
  assert.equal(status.action, "started");
  assertComposeSuffix(fake.composeWrites, ["up", "--detach", "--build"]);
  assertNoCleanup(fake.composeWrites);
});

test("test_start_reuses_stopped_services_without_build", async () => {
  const fake = createFakeRuntime({ snapshot: stoppedServices, probeResults: [unavailable, healthy] });
  const manager = new RuntimeManager({
    ...fake.dependencies,
    startupTimeoutMs: 100,
    pollIntervalMs: 10,
  });

  const status = await manager.start();

  assert.equal(status.phase, "ready");
  assert.equal(status.action, "started");
  assertComposeSuffix(fake.composeWrites, ["up", "--detach"]);
  assertNoCleanup(fake.composeWrites);
});

test("test_foreign_port_is_never_opened", async () => {
  const fake = createFakeRuntime({
    probeResults: [{ api: "foreign", web: "unavailable" }],
  });
  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.phase, "failed");
  assert.equal(status.code, "port-conflict");
  assert.deepEqual(fake.composeWrites, []);
});

test("test_single_healthy_endpoint_requires_action", async () => {
  const fake = createFakeRuntime({
    snapshot: { postgres: "healthy", redis: "healthy", api: "healthy", worker: "healthy", web: "running" },
    probeResults: [{ api: "healthy", web: "unavailable" }],
  });
  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.phase, "needs-action");
  assert.equal(status.code, "partial-runtime");
  assert.equal(status.action, "recover-required");
  assert.deepEqual(fake.composeWrites, []);
});

test("partial runtime requires an explicit recovery action", async () => {
  const fake = createFakeRuntime({
    snapshot: { api: "healthy", web: "stopped", postgres: "healthy", redis: "healthy", worker: "healthy" },
    probeResults: [{ api: "healthy", web: "unavailable" }],
  });

  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.phase, "needs-action");
  assert.equal(status.code, "partial-runtime");
  assert.deepEqual(fake.composeWrites, []);
});

test("test_health_timeout_preserves_stack", async () => {
  const fake = createFakeRuntime({ probeResults: [unavailable] });
  const manager = new RuntimeManager({
    ...fake.dependencies,
    startupTimeoutMs: 20,
    pollIntervalMs: 10,
  });

  const status = await manager.start();

  assert.equal(status.phase, "failed");
  assert.equal(status.code, "health-timeout");
  assertComposeSuffix(fake.composeWrites, ["up", "--detach", "--build"]);
  assert.equal(fake.composeWrites.length, 1);
  assertNoCleanup(fake.composeWrites);
});

test("retrying after a health timeout uses start again and attaches when ready", async () => {
  const fake = createFakeRuntime({ probeResults: [unavailable, unavailable, unavailable, healthy] });
  const manager = new RuntimeManager({
    ...fake.dependencies,
    startupTimeoutMs: 10,
    pollIntervalMs: 10,
  });

  const first = await manager.start();
  const second = await manager.start();

  assert.equal(first.code, "health-timeout");
  assert.equal(second.phase, "ready");
  assert.equal(second.action, "attached");
  assert.equal(fake.composeWrites.length, 1);
  assertNoCleanup(fake.composeWrites);
});

test("port bind failures map to a safe port-conflict status", async () => {
  const fake = createFakeRuntime({
    probeResults: [unavailable],
    composeResults: [{
      exitCode: 1,
      stdout: "",
      stderr: "Bind for 0.0.0.0:8000 failed: port is already allocated",
    }],
  });
  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.code, "port-conflict");
  assert.equal(status.message.includes("already allocated"), false);
  assertComposeSuffix(fake.composeWrites, ["up", "--detach", "--build"]);
  assertNoCleanup(fake.composeWrites);
});

test("failed start never runs cleanup or exposes raw CLI output", async () => {
  const fake = createFakeRuntime({
    probeResults: [unavailable],
    composeResults: [{ exitCode: 1, stdout: "private output", stderr: "private build details" }],
  });
  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.code, "compose-failed");
  assert.equal(status.message.includes("private"), false);
  assertComposeSuffix(fake.composeWrites, ["up", "--detach", "--build"]);
  assert.equal(fake.composeWrites.length, 1);
  assertNoCleanup(fake.composeWrites);
});

test("test_recover_never_removes_volumes", async () => {
  const fake = createFakeRuntime({ probeResults: [healthy] });
  const status = await new RuntimeManager(fake.dependencies).recover();

  assert.equal(status.phase, "ready");
  assert.equal(status.action, "started");
  assertComposeSuffix(fake.composeWrites, ["up", "--detach", "--build"]);
  assertNoCleanup(fake.composeWrites);
});

test("test_stop_uses_compose_stop_only", async () => {
  const fake = createFakeRuntime();
  const status = await new RuntimeManager(fake.dependencies).stop();

  assert.equal(status.phase, "stopped");
  assert.equal(status.action, "stopped");
  assertComposeSuffix(fake.composeWrites, ["stop", "web", "worker", "api", "redis", "postgres"]);
  assertNoCleanup(fake.composeWrites);
});

test("preflight failures do not probe or mutate the runtime", async () => {
  const fake = createFakeRuntime({ preflightResult: Object.assign(new Error("private"), { code: "missing-docker" }) });
  const status = await new RuntimeManager(fake.dependencies).start();

  assert.equal(status.phase, "needs-action");
  assert.equal(status.code, "missing-docker");
  assert.equal(status.message.includes("private"), false);
  assert.equal(fake.probeCalls, 0);
  assert.deepEqual(fake.composeWrites, []);
});
