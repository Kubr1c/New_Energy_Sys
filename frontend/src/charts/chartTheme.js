import { featureSetLabel, modelLabel, targetLabel } from '../utils/displayLabels'

export const chartColors = {
  cyan: '#0891b2',
  green: '#16a34a',
  orange: '#ea580c',
  red: '#dc2626',
  purple: '#7c3aed',
  blue: '#2563eb',
  yellow: '#d97706',
  magenta: '#c026d3',
  teal: '#0d9488',
  indigo: '#4f46e5',
}

export const modelColors = {
  lightgbm: chartColors.yellow,
  lightgbm_tuned: chartColors.yellow,
  xgboost: chartColors.green,
  catboost: chartColors.orange,
  extra_trees: chartColors.purple,
  random_forest: chartColors.blue,
  ridge: chartColors.red,
  elastic_net: chartColors.yellow,
  persistence: '#64748b',
  persistence_baseline: '#64748b',
  tcn: chartColors.cyan,
  dlinear: chartColors.magenta,
  cnn_lstm: chartColors.teal,
  attention_lstm: chartColors.indigo,
}

export function modelColor(name) {
  const key = String(name || '').toLowerCase()
  return modelColors[key] || chartColors.cyan
}

export function fmtNum(value, digits = 4) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

export function shortFeature(featureSet) {
  return featureSetLabel(featureSet)
}

export function shortModel(model) {
  return modelLabel(model)
}

export function shortTarget(target) {
  return targetLabel(target)
}
