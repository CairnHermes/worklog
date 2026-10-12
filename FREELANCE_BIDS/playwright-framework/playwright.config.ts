import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config — the single source of truth for the whole framework.
 *
 * Design goals (per the job spec):
 *  - Robust      -> per-test isolated context, auto-retry, trace on failure
 *  - Scalable    -> projects (chromium/firefox/webkit), parallel workers, sharding for CI
 *  - Fixture-based -> tests receive a `fixture` object (page objects + test data)
 *                      built once per test and torn down cleanly.
 *
 * Sharding: `playwright test --shard=1/3` runs a third of the suite on a CI runner.
 */
export default defineConfig({
  testDir: './tests',
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },

  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
    ['junit', { outputFile: 'test-results/results.xml' }],
  ],

  use: {
    baseURL: process.env.BASE_URL ?? 'https://example.com',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  projects: [
    // API smoke tests first (fast, no browser) to gate expensive UI runs
    {
      name: 'api',
      testMatch: /.*\.api\.spec\.ts/,
    },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
      testIgnore: /.*\.chromium-only\.spec\.ts/,
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      testIgnore: /.*\.chromium-only\.spec\.ts/,
    },
  ],
});
