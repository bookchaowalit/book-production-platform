const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { verifyArtifacts } = require("../scripts/verify-artifacts");
const { version } = require("../package.json");

const SETUP_NAME = "Solo Empire Video Studio Setup.exe";
const PACKAGE_NAME = `SoloEmpireVideoStudio-${version}-full.nupkg`;
const PACKAGE_CONTENT = "nuget package";
const RELEASES_CONTENT = `${crypto.createHash("sha1").update(PACKAGE_CONTENT).digest("hex").toUpperCase()} ${PACKAGE_NAME} ${Buffer.byteLength(PACKAGE_CONTENT)}\n`;
/** @type {Array<[string, string]>} */
const ARTIFACTS = [
  [SETUP_NAME, "unsigned installer"],
  [PACKAGE_NAME, PACKAGE_CONTENT],
  ["RELEASES", RELEASES_CONTENT],
];

function createPackageRoot() {
  const packageRoot = fs.mkdtempSync(path.join(os.tmpdir(), "video-studio-artifacts-"));
  const artifactRoot = path.join(packageRoot, "dist", "forge-out");
  const artifactDirectory = path.join(artifactRoot, "make", "squirrel.windows", "x64");
  fs.mkdirSync(artifactDirectory, { recursive: true });
  fs.writeFileSync(path.join(packageRoot, "package.json"), JSON.stringify({ version }));
  return { packageRoot, artifactRoot, artifactDirectory };
}

/** @param {string} artifactDirectory @param {Array<[string, string]>} [entries] */
function writeArtifacts(artifactDirectory, entries = ARTIFACTS) {
  for (const [name, content] of entries) fs.writeFileSync(path.join(artifactDirectory, name), content);
}

/** @param {string} packageRoot */
function removePackageRoot(packageRoot) {
  fs.rmSync(packageRoot, { recursive: true, force: true });
}

test("verifies exactly one installer, NuGet package, and RELEASES under the fixed x64 directory", () => {
  const { packageRoot, artifactDirectory } = createPackageRoot();
  try {
    writeArtifacts(artifactDirectory);
    const result = verifyArtifacts(packageRoot);
    assert.deepEqual(result, {
      setupPath: path.join(artifactDirectory, SETUP_NAME),
      nupkgPath: path.join(artifactDirectory, PACKAGE_NAME),
      releasesPath: path.join(artifactDirectory, "RELEASES"),
      setupSha256: crypto.createHash("sha256").update("unsigned installer").digest("hex"),
    });
  } finally {
    removePackageRoot(packageRoot);
  }
});

test("rejects missing and duplicate artifact types", () => {
  const missing = createPackageRoot();
  try {
    writeArtifacts(missing.artifactDirectory, ARTIFACTS.slice(0, 2));
    assert.throws(() => verifyArtifacts(missing.packageRoot), /exactly one/i);
  } finally {
    removePackageRoot(missing.packageRoot);
  }

  const duplicate = createPackageRoot();
  try {
    writeArtifacts(duplicate.artifactDirectory, [
      ...ARTIFACTS,
      ["Extra Setup.exe", "duplicate installer"],
      ["another.nupkg", "duplicate package"],
      ["releases", "duplicate release metadata"],
    ]);
    assert.throws(() => verifyArtifacts(duplicate.packageRoot), /exactly one/i);
  } finally {
    removePackageRoot(duplicate.packageRoot);
  }
});

test("rejects a package from another version even when it is the only NuGet file", () => {
  const { packageRoot, artifactDirectory } = createPackageRoot();
  try {
    writeArtifacts(artifactDirectory, [
      [SETUP_NAME, "unsigned installer"],
      ["SoloEmpireVideoStudio-0.1.0-full.nupkg", PACKAGE_CONTENT],
      ["RELEASES", RELEASES_CONTENT.replaceAll(PACKAGE_NAME, "SoloEmpireVideoStudio-0.1.0-full.nupkg")],
    ]);
    assert.throws(() => verifyArtifacts(packageRoot), /version|package/i);
  } finally {
    removePackageRoot(packageRoot);
  }
});

test("rejects RELEASES metadata that does not match the package contents", () => {
  for (const releaseContent of [
    RELEASES_CONTENT.replace(PACKAGE_NAME, `other-${version}-full.nupkg`),
    RELEASES_CONTENT.replace(`${Buffer.byteLength(PACKAGE_CONTENT)}\n`, "999\n"),
    RELEASES_CONTENT.replace(/^[A-F0-9]{40}/, "0".repeat(40)),
  ]) {
    const { packageRoot, artifactDirectory } = createPackageRoot();
    try {
      writeArtifacts(artifactDirectory, [
        [SETUP_NAME, "unsigned installer"],
        [PACKAGE_NAME, PACKAGE_CONTENT],
        ["RELEASES", releaseContent],
      ]);
      assert.throws(() => verifyArtifacts(packageRoot), /RELEASES|metadata|package/i);
    } finally {
      removePackageRoot(packageRoot);
    }
  }
});

test("make runs the real Electron launcher smoke before packaging", () => {
  const { scripts } = require("../package.json");
  assert.match(scripts.make, /npm run smoke:launcher/);
  assert.ok(scripts.make.indexOf("npm run smoke:launcher") < scripts.make.indexOf("electron-forge make"));
});

test("rejects an x64 artifact directory that is a symlink outside forge-out", () => {
  const { packageRoot, artifactRoot, artifactDirectory } = createPackageRoot();
  try {
    const outside = path.join(packageRoot, "outside");
    fs.mkdirSync(outside);
    fs.rmSync(artifactDirectory, { recursive: true, force: true });
    fs.symlinkSync(outside, artifactDirectory, process.platform === "win32" ? "junction" : "dir");
    writeArtifacts(outside);
    assert.throws(() => verifyArtifacts(packageRoot), /symlink|symbolic|outside|path/i);
    assert.equal(fs.existsSync(artifactRoot), true);
  } finally {
    removePackageRoot(packageRoot);
  }
});
