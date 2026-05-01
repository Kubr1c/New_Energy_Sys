import { expect, test } from '@playwright/test'

const ADMIN = { username: 'admin', password: 'admin123' }
const GUEST = { username: 'guest', password: 'guest123' }

async function login(page, account = ADMIN) {
  await page.goto('/#/login')
  await page.evaluate(() => localStorage.clear())
  await page.getByPlaceholder('Enter username').fill(account.username)
  await page.getByPlaceholder('Enter password').fill(account.password)
  await page.getByRole('button', { name: /Sign In/ }).click()
  await expect(page).toHaveURL(/#\/$/)
  await expect(page.locator('body')).toContainText('PV', { timeout: 30_000 })
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
  await expect(page.locator('header')).toContainText('预测监控')
}

async function expectRouteMarker(page, route, marker) {
  await page.goto(route)
  await expect(page.locator('body')).toContainText(marker, { timeout: 30_000 })
}

async function expectNoDocumentOverflow(page) {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))

  // Two pixels of tolerance avoids false positives from browser scrollbar rounding.
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 2)
}

test.describe('NES frontend production contract', () => {
  test('logs in without exposing demo credentials and opens all core routes', async ({ page }) => {
    await page.goto('/#/login')
    await expect(page.locator('body')).not.toContainText('admin/admin123')
    await expect(page.locator('body')).not.toContainText('guest/guest123')

    await login(page)
    await expectRouteMarker(page, '/#/', 'PV')
    await expectRouteMarker(page, '/#/models', 'Model Leaderboard')
    await expectRouteMarker(page, '/#/dispatch', 'Strategy Scores')
    await expectRouteMarker(page, '/#/governance', 'Sensitivity Analysis')
    await expectRouteMarker(page, '/#/data', 'Feature Importance')
    await expectRouteMarker(page, '/#/reports', 'Stages')
  })

  test('keeps the mobile dashboard inside the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await login(page)
    await expectRouteMarker(page, '/#/', 'PV')
    await expectNoDocumentOverflow(page)
  })

  test('sanitizes rendered Markdown reports before injecting HTML', async ({ page }) => {
    await login(page)
    await page.route('**/api/reports/list', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ stage_id: 'xss_probe', name: 'XSS Probe' }]),
      })
    })
    await page.route('**/api/reports/xss_probe/md', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          content: [
            '# Safe Report',
            '<script>window.__nesXss = true</script>',
            '<img src="x" onerror="window.__nesXss = true">',
            '',
            '| Metric | Value |',
            '| --- | --- |',
            '| sanitized | yes |',
          ].join('\n'),
        }),
      })
    })

    await page.goto('/#/reports')
    await page.getByText('XSS Probe').click()
    await expect(page.getByRole('heading', { name: 'Safe Report' })).toBeVisible()

    // The test asserts the DOM contract directly: dangerous elements and inline
    // handlers must be removed, and the injected payload must not execute.
    await expect(page.locator('.markdown-body script')).toHaveCount(0)
    await expect(page.locator('.markdown-body img[onerror]')).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => window.__nesXss === true)).toBe(false)
  })

  test('shows a visible permission error when a guest submits a task', async ({ page }) => {
    await login(page, GUEST)
    await page.goto('/#/data')
    await expect(page.locator('body')).toContainText('Feature Importance', { timeout: 30_000 })

    await page.getByRole('button', { name: 'Run' }).first().click()
    await expect(page.locator('.task-error')).toBeVisible()
  })
})
