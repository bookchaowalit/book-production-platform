const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createLauncherProtocolHandler } = require("../src/protocol/launcher-protocol");

const assets = {
  "index.html": ["<main>Launcher</main>", "text/html; charset=utf-8"],
  "launcher.js": ["document.body.dataset.ready = 'true';", "text/javascript; charset=utf-8"],
  "launcher.css": ["main { color: #123; }", "text/css; charset=utf-8"]
};

/** @param {string} root */
async function createAssets(root) {
  for (const [file, [contents]] of Object.entries(assets)) {
    await fs.mkdir(path.join(root, "src", "launcher"), { recursive: true });
    await fs.mkdir(path.join(root, "resources", "launcher"), { recursive: true });
    await fs.writeFile(path.join(root, "src", "launcher", file), contents);
    await fs.writeFile(path.join(root, "resources", "launcher", file), contents);
  }
}

test("protocol serves each allowlisted asset from the development launcher directory", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "video-launcher-dev-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await createAssets(root);

  const handle = createLauncherProtocolHandler({
    isPackaged: false,
    appPath: root,
    resourcesPath: path.join(root, "resources")
  });

  for (const [file, [contents, contentType]] of Object.entries(assets)) {
    const response = await handle({ url: `solo-video://launcher/${file}` });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), contentType);
    assert.equal(await response.text(), contents);
  }
});

test("protocol serves packaged launcher assets from resources/launcher", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "video-launcher-packaged-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await createAssets(root);

  const handle = createLauncherProtocolHandler({
    isPackaged: true,
    appPath: path.join(root, "unused-app"),
    resourcesPath: path.join(root, "resources")
  });

  const response = await handle({ url: "solo-video://launcher/index.html" });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "<main>Launcher</main>");
});

test("test_launcher_protocol_rejects_path_traversal", async () => {
  /** @type {string[]} */
  const readPaths = [];
  const handle = createLauncherProtocolHandler({
    isPackaged: false,
    appPath: "C:/video-app",
    resourcesPath: "C:/video-resources",
    readFileImpl: async (filePath) => {
      readPaths.push(filePath);
      return Buffer.from("should not be read");
    }
  });
  const rejected = [
    "solo-video://launcher/../index.html",
    "solo-video://launcher/%2e%2e/index.html",
    "solo-video://launcher/%2E%2E%2Findex.html",
    "solo-video://launcher/%2e%2e%5cindex.html",
    "solo-video://launcher/C:/index.html",
    "solo-video://launcher//index.html",
    "file:///C:/video-app/src/launcher/index.html",
    "solo-video://attacker/index.html",
    "solo-video://launcher/index.html?file=launcher.js",
    "solo-video://launcher/index.html#launcher.js",
    "solo-video://launcher/launcher%2ejs",
    "solo-video://launcher/index.html/launcher.js",
    "solo-video://launcher/secret.txt"
  ];

  for (const url of rejected) {
    const response = await handle({ url });
    assert.equal(response.status, 404, url);
  }
  assert.deepEqual(readPaths, []);
});

test("launcher document responses set same-origin CSP and deny objects and frames", async () => {
  const handle = createLauncherProtocolHandler({
    isPackaged: false,
    appPath: "/video-app",
    resourcesPath: "/video-resources",
    readFileImpl: async () => Buffer.from("<main>ok</main>")
  });

  const response = await handle({ url: "solo-video://launcher/index.html" });
  const csp = response.headers.get("content-security-policy");

  assert.ok(csp);
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /script-src 'self'/);
  assert.match(csp, /style-src 'self'/);
  assert.match(csp, /object-src 'none'/);
  assert.match(csp, /frame-src 'none'/);
  assert.doesNotMatch(csp, /https?:|unsafe-eval|\*/);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
});

test("missing allowlisted files return a generic not-found response without a filesystem path", async () => {
  const handle = createLauncherProtocolHandler({
    isPackaged: false,
    appPath: "/video-app",
    resourcesPath: "/video-resources",
    readFileImpl: async () => {
      throw new Error("private path detail");
    }
  });

  const response = await handle({ url: "solo-video://launcher/launcher.js" });
  assert.equal(response.status, 404);
  assert.equal(await response.text(), "Not found");
  assert.equal(response.headers.get("content-security-policy") !== null, true);
});
