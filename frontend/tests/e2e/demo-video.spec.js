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
  { nav: '预测分析', marker: '模型排行榜' },
  { nav: '储能调度', marker: '收益情景' },
  { nav: '配置敏感性', marker: '储能配置敏感性明细' },
  { nav: '数据管理', marker: '预测特征贡献 Top20' },
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
  await page.getByPlaceholder('请输入用户名').fill('admin')
  await page.getByPlaceholder('请输入密码').fill('admin123')
  await page.getByRole('button', { name: /登录系统/ }).click()
  await expect(page).toHaveURL(`${baseURL}/#/`)
  await expect(page.locator('body')).toContainText('储能调度多收益情景量化展示系统', { timeout: 30_000 })
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
