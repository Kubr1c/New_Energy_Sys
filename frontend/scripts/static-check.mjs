import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.cwd()
const read = path => readFileSync(join(root, path), 'utf8')
const failures = []

function assert(condition, message) {
  if (!condition) failures.push(message)
}

const api = read('src/utils/api.js')
const main = read('src/main.js')
const viteConfig = read('vite.config.js')
const reportViewer = read('src/views/ReportViewer.vue')
const markdown = read('src/utils/markdown.js')
const packageJson = read('package.json')
const playwrightConfig = read('playwright.config.js')
const e2eSpec = read('tests/e2e/frontend.spec.js')
const buildArtifactCheck = read('scripts/check-build-artifacts.mjs')
const overviewChart = read('src/charts/overviewCharts.js')
const modelChart = read('src/charts/modelCharts.js')
const dataExplorerService = read('src/services/dataExplorerService.js')
const statefulViews = [
  'src/views/OverviewDashboard.vue',
  'src/views/ModelComparison.vue',
  'src/views/DispatchSimulation.vue',
  'src/views/GovernanceAnalysis.vue',
  'src/views/DataExplorer.vue',
]

assert(!api.includes("'http://localhost:8000'") && !api.includes('"http://localhost:8000"'), 'api.js must not default to http://localhost:8000')
assert(api.includes("|| '/api'"), 'api.js must default to same-origin /api')
assert(api.includes('normalizeApiError'), 'api.js must export normalized API errors')
assert(!main.includes('import * as ElementPlusIconsVue'), 'main.js must not import every Element Plus icon')
assert(main.includes('const icons = {'), 'main.js must register Element Plus icons through an explicit allowlist')
assert(viteConfig.includes('manualChunks'), 'vite.config.js must split production chunks explicitly')
assert(viteConfig.includes("'charts'"), 'vite.config.js must emit a dedicated charts chunk')
assert(viteConfig.includes("'element-plus'"), 'vite.config.js must emit a dedicated Element Plus chunk')
assert(viteConfig.includes("'markdown'"), 'vite.config.js must emit a dedicated Markdown chunk')
assert(reportViewer.includes('renderMarkdown'), 'ReportViewer.vue must render Markdown through renderMarkdown()')
assert(markdown.includes('DOMPurify.sanitize'), 'Markdown renderer must sanitize HTML through DOMPurify')
assert(markdown.includes('FORBID_TAGS'), 'Markdown sanitizer must explicitly forbid dangerous tags')
assert(packageJson.includes('check-build-artifacts'), 'npm run build must execute build artifact checks')
assert(buildArtifactCheck.includes('900'), 'build artifact gate must cap the main entry chunk size')
assert(overviewChart.includes('buildPredictionChartOption'), 'overview chart option must live outside the view')
assert(modelChart.includes('buildModelRadarChartOption'), 'model chart option must live outside the view')
assert(dataExplorerService.includes('fetchDataExplorerBundle'), 'data explorer API calls must live in a page service')
assert(packageJson.includes('"test:e2e"'), 'package.json must expose npm run test:e2e')
assert(playwrightConfig.includes('webServer'), 'Playwright config must own local frontend/backend webServer startup')
assert(e2eSpec.includes('admin/admin123'), 'E2E must verify demo credentials are not visible on the login page')
assert(e2eSpec.includes('XSS Probe'), 'E2E must cover sanitized Markdown report rendering')
assert(e2eSpec.includes('GUEST'), 'E2E must cover visible guest permission failures')
for (const viewPath of statefulViews) {
  const view = read(viewPath)
  assert(view.includes('PageState'), `${viewPath} must render loading/error/empty states through PageState`)
  assert(view.includes('@retry='), `${viewPath} must expose a retry action for recoverable states`)
}

if (failures.length) {
  console.error(failures.map(item => `- ${item}`).join('\n'))
  process.exit(1)
}

console.log('frontend static checks passed')
