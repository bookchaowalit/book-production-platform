const fs = require("node:fs");
const path = require("node:path");

const REQUIRED_FILES = Object.freeze([
  "services/video-postproduction/docker-compose.yml",
  "services/video-postproduction/Dockerfile.web",
  "services/video-postproduction-api/Dockerfile",
  "services/video-postproduction-api/pyproject.toml",
  "services/video-postproduction-api/alembic.ini",
  "services/video-postproduction-api/migrations/versions/0001_v1_core.py",
  "services/video-postproduction-api/migrations/versions/0002_render_outputs.py",
  "services/video-postproduction-api/migrations/versions/0003_project_transcription_quality.py",
  "packages/production-trace/pyproject.toml",
  "apps/video-postproduction-web/package.json",
  "apps/video-postproduction-web/package-lock.json",
  "apps/video-postproduction-web/next.config.ts",
  "apps/video-postproduction-web/postcss.config.mjs",
  "apps/video-postproduction-web/tsconfig.json",
  "apps/video-postproduction-web/lib/api.ts",
]);

const INCLUDED_DIRECTORIES = Object.freeze([
  "services/video-postproduction-api/migrations",
  "services/video-postproduction-api/video_postproduction",
  "packages/production-trace/production_trace",
  "apps/video-postproduction-web/app",
  "apps/video-postproduction-web/lib",
  "apps/video-postproduction-web/components",
  "apps/video-postproduction-web/public",
]);

const ALLOWED_EXTENSIONS = Object.freeze({
  "services/video-postproduction-api/migrations": new Set([".py", ".mako"]),
  "services/video-postproduction-api/video_postproduction": new Set([".py"]),
  "packages/production-trace/production_trace": new Set([".py"]),
  "apps/video-postproduction-web/app": new Set([".ts", ".tsx", ".js", ".jsx", ".css", ".scss"]),
  "apps/video-postproduction-web/lib": new Set([".ts", ".tsx", ".js", ".jsx"]),
  "apps/video-postproduction-web/components": new Set([".ts", ".tsx", ".js", ".jsx", ".css", ".scss"]),
  "apps/video-postproduction-web/public": new Set([".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".avif", ".woff", ".woff2", ".ttf", ".otf"]),
});

const BLOCKED_NAMES = new Set([".git", ".next", ".cache", ".turbo", "__tests__", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", "coverage", "dist", "build", "cache", "out", "tmp", "temp", "tests", "test", "data", "credentials"]);

/** @param {string} name */
function excluded(name) {
  const lower = name.toLowerCase();
  return lower.startsWith(".env") || lower.includes("credential") || lower.includes("secret") || BLOCKED_NAMES.has(lower) || /^(?:test[._-]|.*[._-]test(?:[._-]|$))/.test(lower) || /(?:\.spec\.|\.stories\.)/.test(lower);
}

/** @param {string} repoRoot @param {string} relativePath */
function assertSourcePath(repoRoot, relativePath) {
  let current = repoRoot;
  for (const part of relativePath.split("/")) {
    current = path.join(current, part);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error(`Source contains symbolic link: ${relativePath}`);
  }
  const resolved = path.resolve(current);
  if (!resolved.startsWith(repoRoot + path.sep)) throw new Error(`Source escapes repository: ${relativePath}`);
  return current;
}

/** @param {string} repoRoot */
function collectRuntimeFiles(repoRoot) {
  const root = path.resolve(repoRoot);
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error("Repository root is a symbolic link");
  const files = [...REQUIRED_FILES];
  /** @param {string} relativePath @param {Set<string>} allowedExtensions */
  function visit(relativePath, allowedExtensions) {
    const source = assertSourcePath(root, relativePath);
    if (!fs.statSync(source).isDirectory()) throw new Error(`Expected directory: ${relativePath}`);
    for (const entry of fs.readdirSync(source, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name, "en"))) {
      if (excluded(entry.name)) continue;
      const child = `${relativePath}/${entry.name}`;
      assertSourcePath(root, child);
      if (entry.isDirectory()) visit(child, allowedExtensions);
      else if (entry.isFile()) {
        if (allowedExtensions.has(path.extname(entry.name).toLowerCase())) files.push(child);
      }
      else throw new Error(`Unsupported source entry: ${child}`);
    }
  }
  for (const directory of INCLUDED_DIRECTORIES) {
    if (directory.endsWith("/public") && !fs.existsSync(path.join(root, directory))) continue;
    visit(directory, ALLOWED_EXTENSIONS[/** @type {keyof typeof ALLOWED_EXTENSIONS} */ (directory)]);
  }
  for (const relativePath of REQUIRED_FILES) {
    const source = assertSourcePath(root, relativePath);
    if (!fs.statSync(source).isFile()) throw new Error(`Expected file: ${relativePath}`);
  }
  return [...new Set(files)].sort();
}

module.exports = { REQUIRED_FILES, INCLUDED_DIRECTORIES, collectRuntimeFiles };
