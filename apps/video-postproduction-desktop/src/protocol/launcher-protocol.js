const fs = require("node:fs/promises");
const path = require("node:path");

const LAUNCHER_SCHEME = "solo-video";
const LAUNCHER_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self'",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'none'"
].join("; ");

const ASSET_CONTENT_TYPES = Object.freeze({
  "index.html": "text/html; charset=utf-8",
  "launcher.js": "text/javascript; charset=utf-8",
  "launcher.css": "text/css; charset=utf-8"
});

const SECURITY_HEADERS = Object.freeze({
  "Content-Security-Policy": LAUNCHER_CSP,
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Cache-Control": "no-store"
});

/**
 * @param {{isPackaged: boolean, appPath: string, resourcesPath: string}} options
 * @returns {string}
 */
function resolveLauncherRoot({ isPackaged, appPath, resourcesPath }) {
  if (isPackaged) {
    if (typeof resourcesPath !== "string" || resourcesPath.trim() === "") {
      throw new TypeError("A packaged resources path is required.");
    }
    return path.resolve(resourcesPath, "launcher");
  }

  if (typeof appPath !== "string" || appPath.trim() === "") {
    throw new TypeError("A development app path is required.");
  }
  return path.resolve(appPath, "src", "launcher");
}

/**
 * Return the filename only for the exact supported protocol URLs. Checking the
 * original URL before URL parsing prevents URL normalization from erasing a
 * traversal attempt before the allowlist sees it.
 *
 * @param {unknown} rawUrl
 * @returns {keyof typeof ASSET_CONTENT_TYPES | null}
 */
function getAllowlistedAsset(rawUrl) {
  if (typeof rawUrl !== "string") {
    return null;
  }

  const match = /^solo-video:\/\/launcher\/(index\.html|launcher\.js|launcher\.css)$/.exec(rawUrl);
  if (!match) {
    return null;
  }

  try {
    const parsed = new URL(rawUrl);
    if (
      parsed.protocol !== `${LAUNCHER_SCHEME}:` ||
      parsed.hostname !== "launcher" ||
      parsed.port !== "" ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.pathname !== `/${match[1]}` ||
      parsed.search !== "" ||
      parsed.hash !== ""
    ) {
      return null;
    }
  } catch {
    return null;
  }

  return /** @type {keyof typeof ASSET_CONTENT_TYPES} */ (match[1]);
}

/**
 * @param {{
 *   isPackaged: boolean,
 *   appPath: string,
 *   resourcesPath: string,
 *   readFileImpl?: (filePath: string) => Promise<Uint8Array>
 * }} options
 * @returns {(request: {url?: string}) => Promise<Response>}
 */
function createLauncherProtocolHandler({
  isPackaged,
  appPath,
  resourcesPath,
  readFileImpl = fs.readFile
}) {
  const launcherRoot = resolveLauncherRoot({ isPackaged, appPath, resourcesPath });

  return async function handleLauncherRequest(request) {
    const asset = getAllowlistedAsset(request?.url);
    if (!asset) {
      return new Response("Not found", {
        status: 404,
        headers: SECURITY_HEADERS
      });
    }

    try {
      const fileContents = await readFileImpl(path.join(launcherRoot, asset));
      const body = new Uint8Array(fileContents.byteLength);
      body.set(fileContents);
      return new Response(body, {
        status: 200,
        headers: {
          ...SECURITY_HEADERS,
          "Content-Type": ASSET_CONTENT_TYPES[asset]
        }
      });
    } catch {
      return new Response("Not found", {
        status: 404,
        headers: SECURITY_HEADERS
      });
    }
  };
}

module.exports = {
  LAUNCHER_CSP,
  LAUNCHER_SCHEME,
  createLauncherProtocolHandler
};
