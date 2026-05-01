import { chartColors } from './chartTheme'

export function buildScoreBarOption(scorecard) {
  if (!scorecard.length) return {}

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['Economic', 'Constraint', 'Risk'], top: 0, textStyle: { color: 'rgba(255,255,255,0.78)' } },
    grid: { left: 150, right: 30, top: 44, bottom: 24 },
    xAxis: { type: 'value', max: 100 },
    yAxis: { type: 'category', data: scorecard.map(item => item.scenario_id), axisLabel: { fontSize: 11 } },
    series: [
      { name: 'Economic', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.economic_score) / 3).toFixed(1)), itemStyle: { color: chartColors.cyan } },
      { name: 'Constraint', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.constraint_score) / 3).toFixed(1)), itemStyle: { color: chartColors.green } },
      { name: 'Risk', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.risk_score) / 3).toFixed(1)), itemStyle: { color: chartColors.orange } },
    ],
  }
}

export function buildGovernanceRadarOption(scorecard) {
  if (!scorecard.length) return {}

  const rows = scorecard.slice(0, 4)
  const colors = [chartColors.cyan, chartColors.green, chartColors.orange, chartColors.red]

  return {
    legend: { data: rows.map(item => item.scenario_id), top: 0, textStyle: { fontSize: 10, color: 'rgba(255,255,255,0.78)' } },
    radar: {
      indicator: [
        { name: 'Economic', max: 100 },
        { name: 'Constraint', max: 100 },
        { name: 'Risk', max: 100 },
      ],
      center: ['50%', '56%'],
      radius: '58%',
    },
    series: [
      {
        type: 'radar',
        data: rows.map((item, index) => ({
          name: item.scenario_id,
          value: [Number(item.economic_score), Number(item.constraint_score), Number(item.risk_score)],
          lineStyle: { color: colors[index] },
          itemStyle: { color: colors[index] },
          areaStyle: { color: colors[index], opacity: 0.12 },
        })),
      },
    ],
  }
}

export function buildRawhideRevenueOption(metrics) {
  if (!metrics.length) return {}

  const labels = {
    no_storage: 'No storage',
    stage10_fixed_threshold: 'Stage10 fixed',
    stage11_best_threshold_q40_q95: 'Stage11 upper',
    rolling_optimization: 'Stage12 rolling',
  }
  const order = ['no_storage', 'stage10_fixed_threshold', 'stage11_best_threshold_q40_q95', 'rolling_optimization']
  const rows = order.map(id => metrics.find(item => item.scenario === id)).filter(Boolean)

  return {
    tooltip: { trigger: 'axis', valueFormatter: value => `€${Number(value).toFixed(2)}` },
    grid: { left: 76, right: 24, top: 28, bottom: 68 },
    xAxis: {
      type: 'category',
      data: rows.map(item => labels[item.scenario] || item.scenario),
      axisLabel: { interval: 0, rotate: 16, fontSize: 11 },
    },
    yAxis: { type: 'value', name: 'EUR', axisLabel: { formatter: value => `€${value}` } },
    series: [
      {
        name: 'Incremental revenue',
        type: 'bar',
        data: rows.map(item => Number(item.incremental_revenue_eur || 0).toFixed(2)),
        itemStyle: {
          color: params => {
            const value = Number(rows[params.dataIndex]?.incremental_revenue_eur || 0)
            if (value < 0) return chartColors.red
            if (rows[params.dataIndex]?.scenario === 'stage11_best_threshold_q40_q95') return chartColors.orange
            return chartColors.cyan
          },
        },
        label: { show: true, position: 'top', formatter: params => `€${Number(params.value).toFixed(0)}`, fontSize: 10 },
      },
    ],
  }
}
