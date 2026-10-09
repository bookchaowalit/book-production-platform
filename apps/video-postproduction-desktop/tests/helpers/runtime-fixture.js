const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const sourceFiles = [
  "services/video-postproduction/docker-compose.yml",
  "services/video-postproduction/Dockerfile.web",
  "services/video-postproduction-api/Dockerfile",
  "services/video-postproduction-api/pyproject.toml",
  "services/video-postproduction-api/alembic.ini",
  "services/video-postproduction-api/migrations/env.py",
  "services/video-postproduction-api/migrations/script.py.mako",
  "services/video-postproduction-api/migrations/versions/0001_v1_core.py",
  "services/video-postproduction-api/migrations/versions/0002_render_outputs.py",
  "services/video-postproduction-api/migrations/versions/0003_project_transcription_quality.py",
  "services/video-postproduction-api/migrations/versions/test_schema.py",
  "services/video-postproduction-api/video_postproduction/main.py",
  "services/video-postproduction-api/video_postproduction/secret.json",
  "packages/production-trace/pyproject.toml",
  "packages/production-trace/production_trace/__init__.py",
  "apps/video-postproduction-web/package.json",
  "apps/video-postproduction-web/package-lock.json",
  "apps/video-postproduction-web/next.config.ts",
  "apps/video-postproduction-web/postcss.config.mjs",
  "apps/video-postproduction-web/tsconfig.json",
  "apps/video-postproduction-web/lib/api.ts",
  "apps/video-postproduction-web/lib/api.test.ts",
  "apps/video-postproduction-web/lib/output-format.ts",
  "apps/video-postproduction-web/lib/transcript-range.ts",
  "apps/video-postproduction-web/lib/transcript-range.test.ts",
  "apps/video-postproduction-web/app/page.tsx",
  "apps/video-postproduction-web/app/globals.css",
  "apps/video-postproduction-web/components/card.tsx",
  "apps/video-postproduction-web/components/__tests__/Button.tsx",
  "apps/video-postproduction-web/components/credentials.json",
  "apps/video-postproduction-web/components/session.mp4",
  "apps/video-postproduction-web/components/secret.json",
  "apps/video-postproduction-web/components/test-widget.tsx",
  "apps/video-postproduction-web/components/Build/cache.tsx",
  "apps/video-postproduction-web/app/.NEXT/cache.tsx",
];

async function makeRuntimeFixture({ includeSecret = false, includeNodeModules = false, includeNextBuild = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "video-runtime-fixture-"));
  const packageRoot = path.join(root, "apps", "video-postproduction-desktop");
  fs.mkdirSync(packageRoot, { recursive: true });
  for (const relativePath of sourceFiles) {
    const file = path.join(root, relativePath);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `fixture: ${relativePath}\n`);
  }
  fs.writeFileSync(
    path.join(root, "services", "video-postproduction-api", "pyproject.toml"),
    '[project.optional-dependencies]\ntranscription = [\n  "faster-whisper>=1.1,<2",\n  "av>=11,<19",\n]\n',
  );
  if (includeSecret) fs.writeFileSync(path.join(root, "services", "video-postproduction-api", ".env"), "secret\n");
  if (includeNodeModules) {
    const file = path.join(root, "apps", "video-postproduction-web", "node_modules", "package", "index.js");
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "installed\n");
  }
  if (includeNextBuild) {
    const file = path.join(root, "apps", "video-postproduction-web", ".next", "build.js");
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "built\n");
  }
  return { root, packageRoot };
}

module.exports = { makeRuntimeFixture };
