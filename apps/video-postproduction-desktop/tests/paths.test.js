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
