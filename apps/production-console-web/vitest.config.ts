import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

// Route handlers import through the `@/` alias from tsconfig.json.
export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./', import.meta.url)) } },
});
