import { featureSetLabel, modelLabel } from '../utils/displayLabels'

export const chartColors = {
  cyan: '#00d4ff',
  green: '#00f5a0',
  orange: '#ffa726',
  red: '#ff5252',
  purple: '#b388ff',
  blue: '#64b5f6',
  yellow: '#ffee58',
  magenta: '#ff4fd8',
  teal: '#2dd4bf',
  indigo: '#818cf8',
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
  persistence: '#8a92a6',
  persistence_baseline: '#8a92a6',
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
