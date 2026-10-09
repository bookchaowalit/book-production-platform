const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { collectRuntimeFiles } = require("./runtime-bundle-manifest");

/** @param {string} target */
function assertNoSymlink(target) {
  let current = path.parse(target).root;
  for (const part of path.relative(current, target).split(path.sep)) {
    if (!part) continue;
    current = path.join(current, part);
    if (fs.existsSync(current) || fs.lstatSync(current, { throwIfNoEntry: false })) {
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error(`Destination contains symbolic link: ${current}`);
    }
  }
}

/** @param {{ repoRoot: string, packageRoot: string }} input */
async function stageRuntime({ repoRoot, packageRoot }) {
  const root = path.resolve(repoRoot);
  const packageDirectory = path.resolve(packageRoot);
  const dist = path.resolve(packageDirectory, "dist");
  const output = path.resolve(dist, "video-runtime");
  if (output !== path.join(packageDirectory, "dist", "video-runtime")) throw new Error("Invalid runtime destination");
  if (!packageDirectory.startsWith(root + path.sep)) throw new Error("Package root must be inside repository");
  assertNoSymlink(root);
  assertNoSymlink(packageDirectory);
  assertNoSymlink(dist);
  assertNoSymlink(output);
  const files = collectRuntimeFiles(root);
  const dependencyFile = path.join(root, "services", "video-postproduction-api", "pyproject.toml");
  const dependencies = fs.readFileSync(dependencyFile, "utf8");
  const transcription = dependencies.match(/transcription\s*=\s*\[([\s\S]*?)\]/)?.[1] ?? "";
  if (!/^\s*["']av>=11,<19["'],?\s*$/m.test(transcription)) {
    throw new Error("Runtime is missing the PyAV compatibility pin av>=11,<19");
  }
  fs.mkdirSync(dist, { recursive: true });
  const temporary = fs.mkdtempSync(path.join(dist, ".video-runtime-stage-"));
  try {
    /** @type {Record<string, string>} */
    const hashes = {};
    for (const relativePath of files) {
      const source = path.join(root, relativePath);
      const destination = path.join(temporary, relativePath);
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      const content = fs.readFileSync(source);
      fs.writeFileSync(destination, content);
      hashes[relativePath.replaceAll(path.sep, "/")] = crypto.createHash("sha256").update(content).digest("hex");
    }
    const manifest = { files: hashes };
    const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
    fs.writeFileSync(path.join(temporary, "manifest.json"), serialized);
    assertNoSymlink(output);
    if (fs.existsSync(output)) fs.rmSync(output, { recursive: true });
    for (let attempt = 0; ; attempt += 1) {
      try {
        fs.renameSync(temporary, output);
        break;
      } catch (error) {
        const code = /** @type {NodeJS.ErrnoException} */ (error).code;
        if (!(["EPERM", "EACCES"].includes(code ?? "") && attempt < 10 && !fs.existsSync(output))) throw error;
        await new Promise((resolve) => setTimeout(resolve, 100));
        assertNoSymlink(output);
      }
    }
    return { manifest, hash: crypto.createHash("sha256").update(serialized).digest("hex"), output };
  } finally {
    if (fs.existsSync(temporary)) fs.rmSync(temporary, { recursive: true });
  }
}

if (require.main === module) {
  const packageRoot = path.resolve(__dirname, "..");
  const repoRoot = path.resolve(packageRoot, "..", "..");
  stageRuntime({ repoRoot, packageRoot }).then(({ hash, output }) => {
    process.stdout.write(`${output}\n${hash}\n`);
  }).catch((error) => {
    process.stderr.write(`${error}\n`);
    process.exitCode = 1;
  });
}

module.exports = { stageRuntime };
