const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const SETUP_NAME = "Solo Empire Video Studio Setup.exe";

/** @param {string} parent @param {string} candidate */
function isWithin(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate));
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

/** @param {string} root @param {string} target */
function assertNoSymlinkPath(root, target) {
  const resolvedRoot = path.resolve(root);
  const resolvedTarget = path.resolve(target);
  if (!isWithin(resolvedRoot, resolvedTarget)) throw new Error("artifact path is outside forge-out");

  let current = resolvedRoot;
  const rootStat = fs.lstatSync(current);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) throw new Error("artifact root is invalid");

  const relative = path.relative(resolvedRoot, resolvedTarget);
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error("symlinked artifact path is not allowed");
  }
}

/** @param {string} packageRoot @returns {{ setupPath: string, nupkgPath: string, releasesPath: string, setupSha256: string }} */
function verifyArtifacts(packageRoot) {
  if (typeof packageRoot !== "string" || packageRoot.length === 0) throw new Error("package root is required");
  const root = path.resolve(packageRoot);
  const forgeOut = path.join(root, "dist", "forge-out");
  const artifactDirectory = path.join(forgeOut, "make", "squirrel.windows", "x64");

  try {
    assertNoSymlinkPath(root, artifactDirectory);
  } catch {
    throw new Error("artifact directory is missing, symlinked, or outside forge-out");
  }

  const artifactStat = fs.lstatSync(artifactDirectory);
  if (!artifactStat.isDirectory()) throw new Error("artifact directory is not a directory");
  const entries = fs.readdirSync(artifactDirectory, { withFileTypes: true });
  if (entries.some((entry) => entry.isSymbolicLink())) throw new Error("symlinked artifact files are not allowed");

  const setupFiles = entries.filter((entry) => entry.isFile() && /^.+ setup\.exe$/i.test(entry.name));
  const packages = entries.filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".nupkg"));
  const releases = entries.filter((entry) => entry.isFile() && entry.name.toLowerCase() === "releases");
  if (setupFiles.length !== 1 || setupFiles[0].name !== SETUP_NAME || packages.length !== 1 || releases.length !== 1) {
    throw new Error("expected exactly one Solo Empire Video Studio Setup.exe, one .nupkg, and one RELEASES file");
  }

  const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
  if (typeof version !== "string" || !/^\d+\.\d+\.\d+$/.test(version)) {
    throw new Error("package version is invalid");
  }
  const packageName = `SoloEmpireVideoStudio-${version}-full.nupkg`;
  if (packages[0].name !== packageName) throw new Error("artifact package version does not match package.json");

  const nupkgPath = path.join(artifactDirectory, packageName);
  const releasesPath = path.join(artifactDirectory, releases[0].name);
  const packageContent = fs.readFileSync(nupkgPath);
  const releaseLine = fs.readFileSync(releasesPath, "utf8").trim();
  const expectedReleaseLine = `${crypto.createHash("sha1").update(packageContent).digest("hex").toUpperCase()} ${packageName} ${packageContent.length}`;
  if (releaseLine !== expectedReleaseLine) throw new Error("RELEASES metadata does not match the package");

  const setupPath = path.join(artifactDirectory, setupFiles[0].name);
  return {
    setupPath,
    nupkgPath,
    releasesPath,
    setupSha256: crypto.createHash("sha256").update(fs.readFileSync(setupPath)).digest("hex"),
  };
}

function main() {
  try {
    const result = verifyArtifacts(path.resolve(__dirname, ".."));
    process.stdout.write(`${result.setupPath}\n${result.nupkgPath}\n${result.releasesPath}\nSHA-256 ${result.setupSha256}\n`);
  } catch {
    process.stderr.write("Artifact verification failed.\n");
    process.exitCode = 1;
  }
}

if (require.main === module) main();

module.exports = { verifyArtifacts };
