import { defineConfig, devices } from '@playwright/test'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(frontendDir, '..')
const localBrowserCandidates = [
  process.env.NES_E2E_BROWSER_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean)
const systemBrowserExecutable = localBrowserCandidates.find(candidate => existsSync(candidate))
const systemBrowserLaunchOptions = systemBrowserExecutable
  ? {
      // Local Windows machines often already have Chrome/Edge installed.
      // Point Playwright at that executable so `npm run test:e2e` does not
      // require downloading the Playwright-managed Chromium cache.
      executablePath: systemBrowserExecutable,
    }
  : {}

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'system-chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: systemBrowserLaunchOptions,
      },
    },
  ],
  webServer: [
    {
      command: 'python -m uvicorn new_energy_sys.api.main:app --host 127.0.0.1 --port 8000',
      cwd: projectRoot,
      env: {
        PYTHONPATH: path.join(projectRoot, 'src'),
        NES_APP_ENV: 'development',
      },
      url: 'http://127.0.0.1:8000/docs',
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 3000',
      cwd: frontendDir,
      url: 'http://127.0.0.1:3000',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
})
