import { existsSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const distAssets = join(process.cwd(), 'dist', 'assets')
const failures = []

function assert(condition, message) {
  if (!condition) failures.push(message)
}

function fileSizeKb(file) {
  return statSync(join(distAssets, file)).size / 1024
}

assert(existsSync(distAssets), 'dist/assets is missing; run vite build before checking artifacts')

if (existsSync(distAssets)) {
  const assets = readdirSync(distAssets)
  const jsAssets = assets.filter(file => file.endsWith('.js'))
  const entryChunks = jsAssets.filter(file => /^index-[\w-]+\.js$/.test(file))
  const largestEntryKb = entryChunks.length
    ? Math.max(...entryChunks.map(fileSizeKb))
    : 0

  assert(jsAssets.some(file => file.startsWith('charts-')), 'ECharts/vue-echarts must be emitted as an independent charts chunk')
  assert(jsAssets.some(file => file.startsWith('element-plus-')), 'Element Plus must be emitted as an independent element-plus chunk')
  assert(jsAssets.some(file => file.startsWith('markdown-')), 'marked/DOMPurify must be emitted as an independent markdown chunk')
  assert(jsAssets.some(file => file.startsWith('vue-vendor-')), 'Vue runtime must be emitted as an independent vue-vendor chunk')
  assert(largestEntryKb > 0, 'main entry chunk was not found')
  assert(largestEntryKb <= 900, `main entry chunk must stay <= 900 KiB, got ${largestEntryKb.toFixed(1)} KiB`)
}

if (failures.length) {
  console.error(failures.map(item => `- ${item}`).join('\n'))
  process.exit(1)
}

console.log('frontend build artifact checks passed')
