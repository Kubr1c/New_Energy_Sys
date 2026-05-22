export const EUR_TO_CNY_RATE = 7.9179

function toFiniteNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function eurToCny(value) {
  const n = toFiniteNumber(value)
  return n === null ? 0 : n * EUR_TO_CNY_RATE
}

export function formatYuan(value, digits = 2, fallback = 'N/A') {
  const n = toFiniteNumber(value)
  if (n === null) return fallback
  return `${n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })} 元`
}

export function formatYuanFromEur(value, digits = 2, fallback = 'N/A') {
  const n = toFiniteNumber(value)
  return n === null ? fallback : formatYuan(n * EUR_TO_CNY_RATE, digits, fallback)
}

export function formatYuanPerMwh(value, digits = 1, fallback = 'N/A') {
  const n = toFiniteNumber(value)
  if (n === null) return fallback
  return `${n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })} 元/MWh`
}

export function formatYuanPerMwhFromEur(value, digits = 1, fallback = 'N/A') {
  const n = toFiniteNumber(value)
  return n === null ? fallback : formatYuanPerMwh(n * EUR_TO_CNY_RATE, digits, fallback)
}

export function formatYuanPerUnitFromEur(value, unit, digits = 2, fallback = 'N/A') {
  const n = toFiniteNumber(value)
  if (n === null) return fallback
  return `${(n * EUR_TO_CNY_RATE).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })} 元/${unit}`
}

export function replaceEurUnitsInText(value) {
  return String(value ?? '').replace(/(\d+(?:\.\d+)?)\s*EUR\/kW·年/g, (_, amount) => {
    return formatYuanPerUnitFromEur(amount, 'kW·年')
  })
}
