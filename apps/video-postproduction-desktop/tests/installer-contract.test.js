const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const config = require("../forge.config");
const packageInfo = require("../package.json");
const { APP_USER_MODEL_ID } = require("../src/main");

test("installer targets unsigned per-user Windows Squirrel with the matching app identity", () => {
  assert.deepEqual(config.makers.map((maker) => maker.name), ["@electron-forge/maker-squirrel"]);
  assert.equal(config.outDir, path.resolve(__dirname, "..", "dist", "forge-out"));
  assert.equal(config.makers[0].config.name, "SoloEmpireVideoStudio");
  assert.equal(config.makers[0].config.title, "Solo Empire Video Studio");
  assert.equal(config.makers[0].config.setupExe, "Solo Empire Video Studio Setup.exe");
  assert.equal(config.packagerConfig.executableName, "SoloEmpireVideoStudio");
  assert.deepEqual(config.packagerConfig.extraResource, ["dist/video-runtime", "src/launcher"]);
  assert.equal(config.packagerConfig.asar, true);
  assert.equal(packageInfo.author, "Solo Empire");
  assert.equal(packageInfo.productName, "Solo Empire Video Studio");
  assert.equal(APP_USER_MODEL_ID, "com.squirrel.SoloEmpireVideoStudio.SoloEmpireVideoStudio");

  const makerOptions = config.makers[0].config;
  assert.equal("certificateFile" in makerOptions, false);
  assert.equal("certificatePassword" in makerOptions, false);
  assert.equal("windowsSign" in makerOptions, false);
  assert.equal("perMachine" in makerOptions, false);
});

test("runtime smoke is explicitly opt-in and is not part of the normal test or build scripts", () => {
  assert.equal(packageInfo.scripts["smoke:runtime"], "node scripts/smoke-runtime.js");
  assert.equal(packageInfo.scripts.test.includes("smoke:runtime"), false);
  assert.equal(packageInfo.scripts.make.includes("smoke:runtime"), false);
});
