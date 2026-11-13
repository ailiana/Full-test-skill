// Copy beside ui-smoke.spec.cjs in your test project. Requires @playwright/test.
const path = require('node:path');
const { defineConfig, devices } = require('@playwright/test');
const runDir = path.resolve(process.env.FULL_TEST_RUN_DIR || 'test-results/demo');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'ui-smoke.spec.cjs',
  outputDir: path.join(runDir, 'artifacts'),
  timeout: 15000,
  retries: 0,
  workers: 1,
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(runDir, 'playwright.json') }],
    ['junit', { outputFile: path.join(runDir, 'playwright-junit.xml') }],
    ['html', { outputFolder: path.join(runDir, 'playwright-html'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8765',
    ...(process.env.FULL_TEST_BROWSER_CHANNEL ? { channel: process.env.FULL_TEST_BROWSER_CHANNEL } : {}),
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
