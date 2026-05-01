import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const projectRoot = path.resolve(frontendDir, '..')
const reportsDir = path.join(projectRoot, 'reports')
const demoVideoPath = path.join(reportsDir, 'frontend-demo-1080p.webm')
const recordingDir = path.join(reportsDir, '.frontend-demo-1080p-artifacts')
const recordingViewport = { width: 1920, height: 1080 }

const ROUTES = [
  { nav: '模型评估 Models', marker: 'Model Leaderboard' },
  { nav: '调度收益 Dispatch', marker: 'Strategy Scores' },
  { nav: '配置治理 Governance', marker: 'Sensitivity Analysis' },
  { nav: '数据运维 Data', marker: 'Feature Importance' },
  { nav: '报告归档 Reports', marker: 'Stages' },
]

async function pauseForRecording(page, milliseconds = 1200) {
  // This delay is only for video pacing: charts, route transitions, and page
  // states need enough screen time to be understandable in the final demo.
  // Assertions still rely on visible UI markers, not fixed timing.
  await page.waitForTimeout(milliseconds)
}

async function loginAsAdmin(page, baseURL) {
  await page.goto('/#/login')
  await page.evaluate(() => localStorage.clear())
  await page.getByPlaceholder('Enter username').fill('admin')
  await page.getByPlaceholder('Enter password').fill('admin123')
  await page.getByRole('button', { name: /Sign In/ }).click()
  await expect(page).toHaveURL(`${baseURL}/#/`)
  await expect(page.locator('body')).toContainText('PV', { timeout: 30_000 })
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
  await pauseForRecording(page)
}

test.describe('NES frontend demo recording', () => {
  test('records the primary frontend workflow', async ({ browser, baseURL }) => {
    await mkdir(reportsDir, { recursive: true })
    await mkdir(recordingDir, { recursive: true })

    const context = await browser.newContext({
      viewport: recordingViewport,
      recordVideo: {
        dir: recordingDir,
        size: recordingViewport,
      },
    })
    const page = await context.newPage()

    await loginAsAdmin(page, baseURL)

    for (const route of ROUTES) {
      await page.getByRole('link', { name: route.nav }).click()
      await expect(page.locator('body')).toContainText(route.marker, { timeout: 30_000 })
      await pauseForRecording(page)
    }

    const video = page.video()
    await page.close()
    await context.close()
    await video.saveAs(demoVideoPath)
  })
})
