export const chartColors = {
  cyan: '#00d4ff',
  green: '#00f5a0',
  orange: '#ffa726',
  red: '#ff5252',
  purple: '#b388ff',
  blue: '#64b5f6',
  yellow: '#ffee58',
}

export const modelColors = {
  lightgbm_tuned: chartColors.cyan,
  xgboost: chartColors.green,
  catboost: chartColors.orange,
  extra_trees: chartColors.purple,
  random_forest: chartColors.blue,
  ridge: chartColors.red,
  elastic_net: chartColors.yellow,
  persistence: '#8a92a6',
  cnn_lstm: '#4dd0e1',
  attention_lstm: '#e040fb',
}

export function modelColor(name) {
  return modelColors[name] || chartColors.cyan
}

export function fmtNum(value, digits = 4) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : '-'
}

export function shortFeature(featureSet) {
  const map = {
    history_only: 'history',
    full_features_without_target_plus: 'full',
    weather_history_target_aligned: 'weather',
    persistence_baseline: 'baseline',
  }
  return map[featureSet] || featureSet || '-'
}
