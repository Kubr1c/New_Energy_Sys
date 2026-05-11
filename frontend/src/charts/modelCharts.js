import { fmtNum, modelColor, shortFeature, shortModel } from './chartTheme'

export { fmtNum, modelColor, shortFeature, shortModel }

export function buildModelBarChartOption(metrics) {
  const data = [...metrics].slice(0, 10)
  const values = data.map(row => Number(row.nrmse_capacity || 0))
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const spread = Math.max(maxValue - minValue, 0.001)
  const axisMin = Math.max(0, minValue - Math.max(spread * 0.16, maxValue * 0.015))
  const axisMax = maxValue + Math.max(spread * 0.12, maxValue * 0.012)
  const labels = data.map(row => `${shortModel(row.model)} | ${shortFeature(row.feature_set)}`)

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const item = params?.[0]
        const row = data[item?.dataIndex]
        if (!row) return ''
        return [
          `<strong>${shortModel(row.model)}</strong>`,
          `特征集：${shortFeature(row.feature_set)}`,
          `nRMSE：${fmtNum(row.nrmse_capacity)}`,
          `MAE：${fmtNum(row.mae_kw)}`,
          `RMSE：${fmtNum(row.rmse_kw)}`,
        ].join('<br/>')
      },
    },
    grid: { left: 220, right: 80, top: 28, bottom: 46 },
    xAxis: {
      type: 'value',
      name: 'nRMSE（越低越好）',
      min: axisMin,
      max: axisMax,
      axisLabel: { formatter: value => Number(value).toFixed(3), color: 'rgba(255,255,255,0.72)' },
      nameTextStyle: { color: 'rgba(255,255,255,0.72)' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.10)' } },
    },
    yAxis: {
      type: 'category',
      data: labels,
      inverse: true,
      axisTick: { alignWithLabel: true },
      axisLabel: {
        width: 200,
        overflow: 'truncate',
        align: 'right',
        verticalAlign: 'middle',
        fontSize: 12,
        color: 'rgba(255,255,255,0.82)',
      },
    },
    series: [
      {
        type: 'bar',
        data: data.map((row, index) => ({
          value: values[index],
          itemStyle: { color: modelColor(row.model) },
        })),
        label: {
          show: true,
          position: 'right',
          formatter: params => fmtNum(params.value),
          color: 'rgba(255,255,255,0.86)',
          fontSize: 11,
        },
        barMaxWidth: 18,
        barCategoryGap: '34%',
      },
    ],
  }
}

export function buildModelRadarChartOption(metrics) {
  const candidates = metrics
  if (!candidates.length) return {}

  const indicator = [
    { name: 'nRMSE', max: 0.25 },
    { name: 'MAE', max: 0.15 },
    { name: 'RMSE', max: 0.25 },
    { name: '日间 nRMSE', max: 0.25 },
    { name: '日间 MAPE', max: 1.5 },
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
    legend: {
      data: unique.map(row => shortModel(row.model)),
      top: 0,
      textStyle: { color: 'rgba(255,255,255,0.78)', fontSize: 11 },
    },
    radar: {
      indicator,
      center: ['50%', '55%'],
      radius: '60%',
      axisName: { color: 'rgba(255,255,255,0.76)' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
      splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.05)'] } },
    },
    series: unique.map(row => ({
      name: shortModel(row.model),
      type: 'radar',
      data: [
        {
          name: shortModel(row.model),
          value: [
            row.nrmse_capacity,
            row.mae_kw,
            row.rmse_kw,
            row.daytime_nrmse_capacity,
            row.daytime_mape,
          ].map(value => Number(value || 0)),
        },
      ],
      symbol: 'circle',
      symbolSize: 7,
      lineStyle: { color: modelColor(row.model), width: 2.5 },
      itemStyle: { color: modelColor(row.model), borderColor: '#0a0e27', borderWidth: 1 },
      areaStyle: { color: modelColor(row.model), opacity: 0.1 },
    })),
  }
}
