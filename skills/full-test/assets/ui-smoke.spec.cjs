// Demonstration assertions for demo_app.py ONLY; adapt to the target's contract.
const { test, expect } = require('@playwright/test');

test('UI-001 page loads with usable content and no runtime errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const response = await page.goto('/');
  expect(response.status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Full Test Demo' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add one' })).toBeEnabled();
  expect(errors).toEqual([]);
});

test('UI-002 primary interaction changes visible state', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByTestId('count')).toHaveText('Count: 0');
  await page.getByRole('button', { name: 'Add one' }).click();
  await expect(page.getByTestId('count')).toHaveText('Count: 1');
  const screenshot = testInfo.outputPath('interaction-result.png');
  await page.screenshot({ path: screenshot });
  await testInfo.attach('interaction-result', { path: screenshot, contentType: 'image/png' });
});

test('UI-003 navigation reaches a working endpoint', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Health endpoint' }).click();
  await expect(page).toHaveURL(/\/health$/);
  await expect(page.locator('body')).toContainText('"ok": true');
});
