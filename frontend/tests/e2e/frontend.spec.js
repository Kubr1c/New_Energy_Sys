import { expect, test } from '@playwright/test'

const ADMIN = { username: 'admin', password: 'admin123' }
const GUEST = { username: 'guest', password: 'guest123' }

async function login(page, account = ADMIN) {
  await page.goto('/#/login')
  await page.evaluate(() => localStorage.clear())
  await page.getByPlaceholder('请输入用户名').fill(account.username)
  await page.getByPlaceholder('请输入密码').fill(account.password)
  await page.getByRole('button', { name: /登录系统/ }).click()
  await expect(page).toHaveURL(/#\/$/)
  await expect(page.locator('body')).toContainText('新能源发电预测与储能调度辅助系统', { timeout: 30_000 })
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
  await expect(page.locator('header')).toContainText('系统总览')
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

async function expectChartPainted(page, selector = '.inspection-chart canvas') {
  const canvas = page.locator(selector)
  await expect(canvas).toBeVisible({ timeout: 30_000 })

  const paintStats = await canvas.evaluate(node => {
    const context = node.getContext('2d', { willReadFrequently: true })
    const image = context.getImageData(0, 0, node.width, node.height).data
    const stepX = Math.max(1, Math.floor(node.width / 80))
    const stepY = Math.max(1, Math.floor(node.height / 50))
    let paintedSamples = 0

    // Sample the full canvas instead of checking a single corner: chart grids
    // usually leave transparent padding around the edges, so a corner-only
    // blank check would produce false negatives even when the chart is healthy.
    for (let y = 0; y < node.height; y += stepY) {
      for (let x = 0; x < node.width; x += stepX) {
        const i = (y * node.width + x) * 4
        if (image[i] || image[i + 1] || image[i + 2] || image[i + 3]) {
          paintedSamples += 1
        }
      }
    }

    return { width: node.width, height: node.height, paintedSamples }
  })

  expect(paintStats.width).toBeGreaterThan(300)
  expect(paintStats.height).toBeGreaterThan(300)
  expect(paintStats.paintedSamples).toBeGreaterThan(50)
}

test.describe('NES frontend production contract', () => {
  test('logs in without exposing demo credentials and opens all core routes', async ({ page }) => {
    await page.goto('/#/login')
    await expect(page.locator('body')).not.toContainText('admin/admin123')
    await expect(page.locator('body')).not.toContainText('guest/guest123')

    await login(page)
    await expectRouteMarker(page, '/#/', '新能源发电预测与储能调度辅助系统')
    await expect(page.getByRole('navigation', { name: 'Primary navigation' })).not.toContainText('实验报告')
    await expectRouteMarker(page, '/#/models', '模型排行榜')
    await expectRouteMarker(page, '/#/dispatch', '参考站策略增量收益')
    await page.getByRole('tab', { name: /天气与电价场景/ }).click()
    await expect(page.locator('body')).toContainText('边界提示')
    await expect(page.locator('body')).toContainText('平滑运行调度')
    await expect(page.locator('body')).toContainText('经济优先调度')
    await expect(page.locator('body')).toContainText('天气估算功率与电价曲线')
    await expectRouteMarker(page, '/#/governance', '储能配置推荐边界分析')
    await expectRouteMarker(page, '/#/data', '特征重要性 Top20')
  })

  test('keeps the mobile dashboard inside the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await login(page)
    await expectRouteMarker(page, '/#/', '新能源发电预测与储能调度辅助系统')
    await expectNoDocumentOverflow(page)
  })

  test('renders the prediction inspection chart canvas', async ({ page }) => {
    await login(page)
    await page.goto('/#/inspect')
    await expect(page.locator('body')).toContainText('t+1h RMSE', { timeout: 30_000 })
    await expectChartPainted(page)
  })

  test('renders overview predictions from aligned inspection data', async ({ page }) => {
    const predictionRequests = []
    page.on('request', request => {
      const url = request.url()
      if (url.includes('/api/predictions')) predictionRequests.push(url)
    })

    await login(page)
    await expect(page.locator('body')).toContainText('预测效果概览', { timeout: 30_000 })
    await expectChartPainted(page, '.overview-inspection-chart canvas')
    expect(predictionRequests.some(url => url.includes('/api/predictions/metadata'))).toBeTruthy()
    expect(predictionRequests.some(url => url.includes('/api/predictions/inspect'))).toBeTruthy()
    expect(predictionRequests.some(url => url.includes('/api/predictions/main'))).toBeFalsy()

    const before = predictionRequests.filter(url => url.includes('/api/predictions/inspect')).length
    await page.getByTestId('overview-prev-day').click()
    await expect.poll(() =>
      predictionRequests.filter(url => url.includes('/api/predictions/inspect')).length
    ).toBeGreaterThan(before)
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
    await page.getByRole('button', { name: /XSS Probe/ }).click()
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
    await expect(page.locator('body')).toContainText('特征重要性 Top20', { timeout: 30_000 })

    await page.getByRole('button', { name: /开始训练|开始运行/ }).first().click()
    await expect(page.locator('.task-error')).toBeVisible()
  })
})
