import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.cwd()
const read = path => readFileSync(join(root, path), 'utf8')
const failures = []

function assert(condition, message) {
  if (!condition) failures.push(message)
}

const api = read('src/utils/api.js')
const reportViewer = read('src/views/ReportViewer.vue')
const markdown = read('src/utils/markdown.js')
const packageJson = read('package.json')
const playwrightConfig = read('playwright.config.js')
const e2eSpec = read('tests/e2e/frontend.spec.js')
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
assert(reportViewer.includes('renderMarkdown'), 'ReportViewer.vue must render Markdown through renderMarkdown()')
assert(markdown.includes('DOMPurify.sanitize'), 'Markdown renderer must sanitize HTML through DOMPurify')
assert(markdown.includes('FORBID_TAGS'), 'Markdown sanitizer must explicitly forbid dangerous tags')
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
