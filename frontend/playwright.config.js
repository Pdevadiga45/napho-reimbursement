import { defineConfig } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';

const frontend = process.cwd();
const backend = resolve(frontend, '..', 'backend');
const database = resolve(frontend, 'test-results', `claims-${process.pid}-${randomUUID()}.sqlite3`);
const backendUrl = 'http://127.0.0.1:18000';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  outputDir: './test-results/playwright',
  use: {
    baseURL: 'http://127.0.0.1:15173',
    browserName: 'chromium',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uv run --project . uvicorn app:app --host 127.0.0.1 --port 18000',
      cwd: backend,
      env: {
        NAPHO_DB_PATH: database,
        UV_CACHE_DIR: resolve(frontend, 'test-results', 'uv-cache'),
      },
      url: `${backendUrl}/api/auth/me`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 15173 --strictPort',
      cwd: frontend,
      env: { NAPHO_API_TARGET: backendUrl },
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
