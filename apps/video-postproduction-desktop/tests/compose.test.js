const assert = require("node:assert/strict");
const test = require("node:test");
const { composeArgs, inspectComposeProject, parseComposePs } = require("../src/runtime/compose");

const paths = {
  bundleRoot: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime",
  composeFile: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime\\services\\video-postproduction\\docker-compose.yml",
  composeProjectDirectory: "C:\\Program Files\\Solo Empire Video Studio\\resources\\video-runtime\\services\\video-postproduction",
};

test("pins the production project name and paths before the Compose subcommand", () => {
  assert.deepEqual(
    composeArgs(paths, "video-postproduction", ["ps", "--all", "--format", "json"]),
    [
      "compose", "--project-name", "video-postproduction", "--file", paths.composeFile,
      "--project-directory", paths.composeProjectDirectory, "ps", "--all", "--format", "json",
    ],
  );
});

test("adds only the fixed smoke override for the separately named smoke project", () => {
  const smokePaths = {
    composeFile: "C:\\bundle\\services\\video-postproduction\\docker-compose.yml",
    composeFiles: [
      "C:\\bundle\\services\\video-postproduction\\docker-compose.yml",
      "C:\\repo\\tests\\fixtures\\docker-compose.native-smoke.override.yml",
    ],
    composeProjectDirectory: "C:\\bundle\\services\\video-postproduction",
  };

  assert.deepEqual(
    composeArgs(smokePaths, "video-postproduction-native-smoke-test", ["ps", "--all", "--format", "json"]),
    [
      "compose", "--project-name", "video-postproduction-native-smoke-test",
      "--file", smokePaths.composeFiles[0],
      "--file", smokePaths.composeFiles[1],
      "--project-directory", smokePaths.composeProjectDirectory, "ps", "--all", "--format", "json",
    ],
  );
});

test("does not allow a smoke override on the existing production project", () => {
  assert.throws(() => composeArgs({
    ...paths,
    composeFiles: [
      paths.composeFile,
      "C:\\repo\\tests\\fixtures\\docker-compose.native-smoke.override.yml",
    ],
  }, "video-postproduction", ["ps", "--all", "--format", "json"]));
});

test("rejects a non-fixed Compose override for the smoke project", () => {
  assert.throws(() => composeArgs({
    ...paths,
    composeFiles: [paths.composeFile, "C:\\repo\\arbitrary.override.yml"],
  }, "video-postproduction-native-smoke-test", ["ps", "--all", "--format", "json"]));
});

test("rejects project names outside the two fixed runtime identities", () => {
  assert.throws(() => composeArgs(paths, "user-controlled-project", ["ps"]), /invalid Compose project name/);
});

test("parses NDJSON and maps all five declared services to the fixed status union", () => {
  const snapshot = parseComposePs([
    '{"Service":"postgres","State":"running","Health":"healthy"}',
    '{"Service":"redis","State":"running","Health":"starting"}',
    '{"Service":"api","State":"running","Health":"unhealthy"}',
    '{"Service":"worker","State":"exited","Health":""}',
    '{"Service":"web","State":"created"}',
  ].join("\n"));

  assert.deepEqual(snapshot, {
    postgres: "healthy",
    redis: "running",
    api: "unhealthy",
    worker: "stopped",
    web: "stopped",
  });
});

test("returns unknown for all services when Compose stdout is empty", () => {
  assert.deepEqual(parseComposePs(" \r\n\t\n"), {
    postgres: "unknown",
    redis: "unknown",
    api: "unknown",
    worker: "unknown",
    web: "unknown",
  });
});

test("rejects malformed Compose JSON without echoing the input line", () => {
  assert.throws(
    () => parseComposePs('{"Service":"api","State":"running"}\nprivate-cli-output'),
    (error) => error instanceof Error && !error.message.includes("private-cli-output"),
  );
});

test("rejects services not declared by this runtime", () => {
  assert.throws(
    () => parseComposePs('{"Service":"unrelated","State":"running"}'),
    (error) => error instanceof Error && !error.message.includes("unrelated"),
  );
});

test("rejects unknown Compose states instead of treating them as healthy", () => {
  assert.throws(
    () => parseComposePs('{"Service":"api","State":"ready","Health":"healthy"}'),
    (error) => error instanceof Error && !error.message.includes("ready"),
  );
});

test("rejects duplicate service rows rather than choosing an arbitrary state", () => {
  assert.throws(
    () => parseComposePs([
      '{"Service":"api","State":"running","Health":"healthy"}',
      '{"Service":"api","State":"exited"}',
    ].join("\n")),
    (error) => error instanceof Error && !error.message.includes("api"),
  );
});

test("inspects only the fixed Compose project and returns the parsed snapshot", async () => {
  /** @type {string[][]} */
  const calls = [];
  const docker = {
    /** @param {string[]} args */
    run: async (args) => {
      calls.push(args);
      return {
        exitCode: 0,
        signal: null,
        stdout: '{"Service":"api","State":"running","Health":"healthy"}',
        stderr: "",
      };
    },
  };

  const snapshot = await inspectComposeProject({ dockerCli: docker, paths });

  assert.deepEqual(calls, [[
    "compose", "--project-name", "video-postproduction", "--file", paths.composeFile,
    "--project-directory", paths.composeProjectDirectory, "ps", "--all", "--format", "json",
  ]]);
  assert.equal(snapshot.api, "healthy");
  assert.equal(snapshot.web, "unknown");
});

test("turns a nonzero Compose inspection into a safe error", async () => {
  const docker = {
    run: async () => ({
      exitCode: 17,
      signal: null,
      stdout: "private stdout payload",
      stderr: "private stderr payload",
    }),
  };

  await assert.rejects(
    inspectComposeProject({ dockerCli: docker, paths }),
    (error) => error instanceof Error
      && !error.message.includes("private"),
  );
});
