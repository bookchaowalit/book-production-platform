const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { stageRuntime } = require("../scripts/stage-runtime");
const { makeRuntimeFixture } = require("./helpers/runtime-fixture");

test("stages the current PyAV compatibility pin in the transcription dependency bundle", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const relativePath = path.join("services", "video-postproduction-api", "pyproject.toml");
  const source = fs.readFileSync(path.resolve(__dirname, "..", "..", "..", relativePath), "utf8");
  fs.writeFileSync(path.join(root, relativePath), source);
  const { output, manifest } = await stageRuntime({ repoRoot: root, packageRoot });
  const staged = fs.readFileSync(path.join(output, relativePath), "utf8");
  assert.equal(staged, source);
  assert.match(staged.match(/transcription\s*=\s*\[([\s\S]*?)\]/)?.[1] ?? "", /^\s*"av>=11,<19",\s*$/m);
  assert.equal(manifest.files[relativePath.replaceAll(path.sep, "/")], crypto.createHash("sha256").update(source).digest("hex"));
});

test("rejects a missing PyAV pin before replacing an existing runtime", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = await stageRuntime({ repoRoot: root, packageRoot });
  const dependencyFile = path.join(root, "services", "video-postproduction-api", "pyproject.toml");
  fs.writeFileSync(dependencyFile, '[project.optional-dependencies]\ntranscription = ["faster-whisper>=1.1,<2"]\n');
  await assert.rejects(stageRuntime({ repoRoot: root, packageRoot }), /PyAV compatibility pin/);
  assert.equal(crypto.createHash("sha256").update(fs.readFileSync(path.join(first.output, "manifest.json"))).digest("hex"), first.hash);
});

test("packages current Thai caption dependencies, font installation and all required migrations", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const sourceRoot = path.resolve(__dirname, "..", "..", "..");
  const relativePaths = [
    "services/video-postproduction-api/pyproject.toml",
    "services/video-postproduction-api/Dockerfile",
    "services/video-postproduction-api/migrations/versions/0001_v1_core.py",
    "services/video-postproduction-api/migrations/versions/0002_render_outputs.py",
    "services/video-postproduction-api/migrations/versions/0003_project_transcription_quality.py",
  ];
  for (const relativePath of relativePaths) {
    fs.copyFileSync(path.join(sourceRoot, relativePath), path.join(root, relativePath));
  }
  const { output, manifest } = await stageRuntime({ repoRoot: root, packageRoot });
  for (const relativePath of relativePaths) {
    const source = fs.readFileSync(path.join(sourceRoot, relativePath));
    assert.deepEqual(fs.readFileSync(path.join(output, relativePath)), source);
    assert.equal(manifest.files[relativePath], crypto.createHash("sha256").update(source).digest("hex"));
  }
  assert.match(fs.readFileSync(path.join(output, relativePaths[0]), "utf8"), /"pythainlp>=5\.1,<6"/);
  assert.match(fs.readFileSync(path.join(output, relativePaths[1]), "utf8"), /\bfonts-noto-core\b/);
});

test("rejects missing required migrations before replacing an existing runtime", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = await stageRuntime({ repoRoot: root, packageRoot });
  fs.rmSync(path.join(root, "services", "video-postproduction-api", "migrations", "versions", "0003_project_transcription_quality.py"));
  await assert.rejects(stageRuntime({ repoRoot: root, packageRoot }), /ENOENT/);
  assert.equal(crypto.createHash("sha256").update(fs.readFileSync(path.join(first.output, "manifest.json"))).digest("hex"), first.hash);
});

test("stages only required runtime source and excludes machine/user state", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture({ includeSecret: true, includeNodeModules: true, includeNextBuild: true });
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(packageRoot, "dist", "video-runtime");
  const result = await stageRuntime({ repoRoot: root, packageRoot });

  for (const relativePath of [
    "services/video-postproduction/docker-compose.yml",
    "services/video-postproduction-api/video_postproduction/main.py",
    "services/video-postproduction-api/migrations/script.py.mako",
    "apps/video-postproduction-web/package-lock.json",
    "apps/video-postproduction-web/app/globals.css",
    "apps/video-postproduction-web/lib/output-format.ts",
    "apps/video-postproduction-web/lib/transcript-range.ts",
  ]) assert.equal(fs.existsSync(path.join(output, relativePath)), true, relativePath);
  for (const relativePath of [
    "apps/video-postproduction-web/lib/api.test.ts",
    "apps/video-postproduction-web/lib/transcript-range.test.ts",
    "apps/video-postproduction-web/components/credentials.json",
    "apps/video-postproduction-web/components/session.mp4",
    "apps/video-postproduction-web/components/secret.json",
    "apps/video-postproduction-web/components/test-widget.tsx",
    "apps/video-postproduction-web/components/Build/cache.tsx",
    "apps/video-postproduction-web/components/__tests__/Button.tsx",
    "apps/video-postproduction-web/app/.NEXT/cache.tsx",
    "services/video-postproduction-api/video_postproduction/secret.json",
    "services/video-postproduction-api/migrations/versions/test_schema.py",
    "services/video-postproduction-api/.env",
    "apps/video-postproduction-web/node_modules",
    "apps/video-postproduction-web/.next",
  ]) assert.equal(fs.existsSync(path.join(output, relativePath)), false, relativePath);
  const manifest = JSON.parse(fs.readFileSync(path.join(output, "manifest.json"), "utf8"));
  for (const relativePath of [
    "apps/video-postproduction-web/components/session.mp4",
    "apps/video-postproduction-web/components/secret.json",
    "apps/video-postproduction-web/components/test-widget.tsx",
    "apps/video-postproduction-web/components/Build/cache.tsx",
    "apps/video-postproduction-web/components/__tests__/Button.tsx",
    "apps/video-postproduction-web/app/.NEXT/cache.tsx",
    "services/video-postproduction-api/video_postproduction/secret.json",
    "services/video-postproduction-api/migrations/versions/test_schema.py",
  ]) assert.equal(manifest.files[relativePath], undefined, relativePath);
  assert.equal(manifest.files["apps/video-postproduction-web/lib/api.ts"], crypto.createHash("sha256").update("fixture: apps/video-postproduction-web/lib/api.ts\n").digest("hex"));
  assert.equal(manifest.files["manifest.json"], undefined);
  assert.equal(result.hash, crypto.createHash("sha256").update(fs.readFileSync(path.join(output, "manifest.json"))).digest("hex"));
});

test("smoke override declares the fixed isolated project name", () => {
  const override = fs.readFileSync(path.join(__dirname, "fixtures", "docker-compose.native-smoke.override.yml"), "utf8");
  assert.match(override, /^name: video-postproduction-native-smoke-test\r?$/m);
});

test("repeat staging produces the same manifest and removes stale generated files", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = await stageRuntime({ repoRoot: root, packageRoot });
  const stale = path.join(packageRoot, "dist", "video-runtime", "stale.txt");
  fs.writeFileSync(stale, "stale");
  const second = await stageRuntime({ repoRoot: root, packageRoot });
  assert.equal(second.hash, first.hash);
  assert.equal(fs.existsSync(stale), false);
});

test("rejects symlinked source and destination without deleting their targets", async (t) => {
  const { root, packageRoot } = await makeRuntimeFixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "apps", "video-postproduction-web", "components");
  const target = path.join(root, "target-directory");
  fs.mkdirSync(target);
  fs.writeFileSync(path.join(target, "safe.txt"), "safe");
  fs.rmSync(source, { recursive: true });
  fs.symlinkSync(target, source, "junction");
  await assert.rejects(stageRuntime({ repoRoot: root, packageRoot }), /symbolic link/i);
  assert.equal(fs.readFileSync(path.join(target, "safe.txt"), "utf8"), "safe");
  fs.rmSync(source);
  fs.mkdirSync(source);
  const dist = path.join(packageRoot, "dist");
  fs.mkdirSync(dist, { recursive: true });
  fs.symlinkSync(root, path.join(dist, "video-runtime"), "junction");
  await assert.rejects(stageRuntime({ repoRoot: root, packageRoot }), /symbolic link/i);
  assert.equal(fs.readFileSync(path.join(target, "safe.txt"), "utf8"), "safe");
});
