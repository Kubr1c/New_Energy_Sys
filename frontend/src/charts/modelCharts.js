import { fmtNum, modelColor, shortFeature } from './chartTheme'

export { fmtNum, modelColor, shortFeature }

export function buildModelBarChartOption(metrics) {
  const data = [...metrics]
    .sort((a, b) => (a.nrmse_capacity || 0) - (b.nrmse_capacity || 0))
    .slice(0, 12)
  const labels = data.map(row => `${row.model}\n(${shortFeature(row.feature_set)})`)

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 170, right: 30, top: 24, bottom: 36 },
    xAxis: { type: 'value', name: 'nRMSE' },
    yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, color: 'rgba(255,255,255,0.78)' } },
    series: [
      {
        type: 'bar',
        data: data.map(row => ({
          value: fmtNum(row.nrmse_capacity),
          itemStyle: { color: modelColor(row.model) },
        })),
        barMaxWidth: 20,
      },
    ],
  }
}

export function buildModelRadarChartOption(metrics) {
  const candidates = metrics.filter(row => (
    row.feature_set === 'history_only' ||
    row.feature_set === 'weather_history_target_aligned'
  ))
  if (!candidates.length) return {}

  const indicator = [
    { name: 'nRMSE', max: 0.25 },
    { name: 'MAE', max: 0.15 },
    { name: 'RMSE', max: 0.25 },
    { name: 'Day nRMSE', max: 0.25 },
    { name: 'Day MAPE', max: 1.5 },
  ]

  const seen = new Set()
  const unique = candidates
    .filter(row => {
      if (seen.has(row.model)) return false
      seen.add(row.model)
      return true
    })
    .slice(0, 5)

  return {
    legend: { data: unique.map(row => row.model), top: 0, textStyle: { fontSize: 11 } },
    radar: { indicator, center: ['50%', '55%'], radius: '60%' },
    series: [
      {
        type: 'radar',
        data: unique.map(row => ({
          name: row.model,
          value: [
            row.nrmse_capacity,
            row.mae_kw,
            row.rmse_kw,
            row.daytime_nrmse_capacity,
            row.daytime_mape,
          ].map(value => Number(value || 0)),
          lineStyle: { color: modelColor(row.model) },
          itemStyle: { color: modelColor(row.model) },
          areaStyle: { color: modelColor(row.model), opacity: 0.1 },
        })),
      },
    ],
  }
}
