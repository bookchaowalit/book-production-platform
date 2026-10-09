const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const test = require("node:test");
const { PassThrough } = require("node:stream");
const { DockerCli, DockerCliError, findDockerExecutable } = require("../src/runtime/docker-cli");

/** @typedef {import("node:events").EventEmitter & { stdout: PassThrough, stderr: PassThrough, kill: (signal?: NodeJS.Signals) => boolean }} FakeChild */

/**
 * @param {{ stdout?: string, stderr?: string, exitCode?: number, onKill?: (signal?: NodeJS.Signals) => void }} [options]
 * @returns {FakeChild}
 */
function fakeChild({ stdout = "", stderr = "", exitCode = 0, onKill } = {}) {
  const child = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    kill(/** @type {NodeJS.Signals | undefined} */ signal) {
      onKill?.(signal);
      queueMicrotask(() => child.emit("close", null, signal ?? null));
      return true;
    },
  });
  queueMicrotask(() => {
    child.stdout.end(stdout);
    child.stderr.end(stderr);
    child.emit("close", exitCode, null);
  });
  return child;
}

/** @param {unknown} error @param {string} code @returns {boolean} */
function hasErrorCode(error, code) {
  return error instanceof DockerCliError && error.code === code;
}

test("test_path_with_spaces_remains_one_argument", () => {
  const found = findDockerExecutable({
    platform: "win32",
    pathEntries: ["C:\\Program Files\\Docker\\Docker\\resources\\bin"],
    exists: (candidate) => candidate === "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
  });

  assert.equal(found, "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe");
});

test("searches PATH before the Docker Desktop Program Files fallback", () => {
  /** @type {string[]} */
  const checked = [];
  const found = findDockerExecutable({
    platform: "win32",
    pathEntries: ["C:\\tools", "C:\\Program Files\\Docker\\Docker\\resources\\bin"],
    programFiles: "C:\\Program Files",
    exists: (candidate) => {
      checked.push(candidate);
      return candidate.endsWith("\\docker.exe");
    },
  });

  assert.equal(found, "C:\\tools\\docker.exe");
  assert.deepEqual(checked, ["C:\\tools\\docker.exe"]);
});

test("discovers Docker Desktop from Program Files when PATH has no Docker CLI", () => {
  const fallback = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe";
  const found = findDockerExecutable({
    platform: "win32",
    pathEntries: ["C:\\tools"],
    programFiles: "C:\\Program Files",
    exists: (candidate) => candidate === fallback,
  });

  assert.equal(found, fallback);
});

test("returns null when no Docker executable is present", () => {
  assert.equal(findDockerExecutable({ platform: "linux", pathEntries: ["/usr/bin"], exists: () => false }), null);
});

test("runs the executable with shell disabled and hides the console window", async () => {
  /** @type {{ exe: string, args: string[], options: { shell: boolean, windowsHide: boolean, stdio: string[] } } | undefined} */
  let observed;
  const cli = new DockerCli({
    executable: "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
    spawnImpl: (exe, args, options) => {
      observed = { exe, args, options };
      return fakeChild();
    },
  });

  const result = await cli.run(["compose", "version"]);

  assert.ok(observed);
  assert.equal(observed.exe.includes("Program Files"), true);
  assert.deepEqual(observed.args, ["compose", "version"]);
  assert.equal(observed.options.shell, false);
  assert.equal(observed.options.windowsHide, true);
  assert.deepEqual(observed.options.stdio, ["ignore", "pipe", "pipe"]);
  assert.equal(result.exitCode, 0);
});

test("retains only the last 64 KiB of each output stream", async () => {
  const stdout = `${"a".repeat(65 * 1024)}tail-out`;
  const stderr = `${"b".repeat(65 * 1024)}tail-err`;
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => fakeChild({ stdout, stderr }) });

  const result = await cli.run(["info"]);

  assert.equal(Buffer.byteLength(result.stdout), 64 * 1024);
  assert.equal(result.stdout.endsWith("tail-out"), true);
  assert.equal(Buffer.byteLength(result.stderr), 64 * 1024);
  assert.equal(result.stderr.endsWith("tail-err"), true);
});

test("times out a hung process and kills its child", async () => {
  let killedWith;
  const child = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    kill(/** @type {NodeJS.Signals | undefined} */ signal) {
      killedWith = signal;
      return true;
    },
  });
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => child });

  await assert.rejects(cli.run(["info"], { timeoutMs: 5 }), (error) => hasErrorCode(error, "compose-failed"));
  assert.equal(killedWith, "SIGKILL");
});

test("test_missing_cli_is_typed_error", async () => {
  let spawned = false;
  const cli = new DockerCli({ executable: null, spawnImpl: () => { spawned = true; return fakeChild(); } });

  await assert.rejects(cli.preflight(), (error) => {
    assert.ok(error instanceof DockerCliError);
    assert.equal(error.code, "missing-docker");
    assert.equal(error.message, "ไม่พบ Docker Desktop หรือ Docker CLI ใน PATH");
    return true;
  });
  assert.equal(spawned, false);
});

test("preflight runs Compose v2 and Linux engine checks with fixed arguments", async () => {
  /** @type {string[][]} */
  const calls = [];
  const results = [
    { exitCode: 0, stdout: "Docker Compose version v2.30.3\n", stderr: "" },
    { exitCode: 0, stdout: "linux\n", stderr: "" },
  ];
  const cli = new DockerCli({
    executable: "docker",
    spawnImpl: (_exe, args) => {
      calls.push(args);
      return fakeChild(results[calls.length - 1]);
    },
  });

  const result = await cli.preflight();

  assert.deepEqual(calls, [["compose", "version"], ["info", "--format", "{{.OSType}}"]]);
  assert.deepEqual(result, { composeVersion: "2.30.3", engine: "linux" });
});

test("preflight accepts the Compose v5 bundled with current Docker Desktop", async () => {
  const results = [
    { exitCode: 0, stdout: "Docker Compose version v5.5.1\n", stderr: "" },
    { exitCode: 0, stdout: "linux\n", stderr: "" },
  ];
  let index = 0;
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => fakeChild(results[index++]) });

  assert.deepEqual(await cli.preflight(), { composeVersion: "5.5.1", engine: "linux" });
  assert.equal(index, 2);
});

test("test_engine_must_report_linux", async () => {
  const results = [
    { exitCode: 0, stdout: "Docker Compose version v2.30.3", stderr: "" },
    { exitCode: 0, stdout: "windows", stderr: "" },
  ];
  let index = 0;
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => fakeChild(results[index++]) });

  await assert.rejects(cli.preflight(), (error) => hasErrorCode(error, "engine-unavailable"));
});

test("preflight reports a daemon command failure as a typed error", async () => {
  const results = [
    { exitCode: 0, stdout: "Docker Compose version v2.30.3", stderr: "" },
    { exitCode: 1, stdout: "", stderr: "Cannot connect to the Docker daemon" },
  ];
  let index = 0;
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => fakeChild(results[index++]) });

  await assert.rejects(cli.preflight(), (error) => hasErrorCode(error, "engine-unavailable"));
});

test("rejects Compose v1 without attempting engine-dependent work", async () => {
  let calls = 0;
  const cli = new DockerCli({
    executable: "docker",
    spawnImpl: () => {
      calls += 1;
      return fakeChild({ stdout: "Docker Compose version v1.29.2" });
    },
  });

  await assert.rejects(cli.preflight(), (error) => hasErrorCode(error, "compose-failed"));
  assert.equal(calls, 1);
});

test("does not accept an unrelated version number as the Compose version", async () => {
  const cli = new DockerCli({
    executable: "docker",
    spawnImpl: () => fakeChild({ stdout: "Docker 26.1.0; version details unavailable" }),
  });

  await assert.rejects(cli.preflight(), (error) => hasErrorCode(error, "compose-failed"));
});

test("preflight errors do not expose raw CLI output", async () => {
  const results = [
    { exitCode: 0, stdout: "Docker Compose version v2.30.3", stderr: "" },
    { exitCode: 1, stdout: "", stderr: "API_TOKEN=private-token raw daemon response" },
  ];
  let index = 0;
  const cli = new DockerCli({ executable: "docker", spawnImpl: () => fakeChild(results[index++]) });

  await assert.rejects(cli.preflight(), (error) => {
    assert.ok(error instanceof DockerCliError);
    assert.equal(error.code, "engine-unavailable");
    assert.equal(error.message.includes("private-token"), false);
    assert.equal(error.message.includes("raw daemon response"), false);
    return true;
  });
});
