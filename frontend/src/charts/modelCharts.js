import { fmtNum, modelColor, shortFeature, shortModel, shortTarget } from './chartTheme'

export { fmtNum, modelColor, shortFeature, shortModel, shortTarget }

export function buildModelBarChartOption(metrics) {
  const data = [...metrics].slice(0, 10)
  const values = data.map(row => Number(row.nrmse_capacity || 0))
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const spread = Math.max(maxValue - minValue, 0.001)
  const axisMin = Math.max(0, minValue - Math.max(spread * 0.16, maxValue * 0.015))
  const axisMax = maxValue + Math.max(spread * 0.12, maxValue * 0.012)
  const labels = data.map(row => `${shortModel(row.model)} | ${shortTarget(row.target)} | ${shortFeature(row.feature_set)}`)

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const item = params?.[0]
        const row = data[item?.dataIndex]
        if (!row) return ''
        return [
          `<strong>${shortModel(row.model)}</strong>`,
          `预测时长：${shortTarget(row.target)}`,
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
      axisLabel: { formatter: value => Number(value).toFixed(3), color: '#606266' },
      nameTextStyle: { color: '#606266' },
      splitLine: { lineStyle: { color: '#ebeef5' } },
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
        color: '#606266',
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
          color: '#303133',
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

  const seen = new Set()
  const unique = candidates
    .filter(row => {
      const key = `${row.model}-${row.target}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .slice(0, 5)
  const metricDefs = [
    { name: 'nRMSE', key: 'nrmse_capacity', minMax: 0.25 },
    { name: 'MAE', key: 'mae_kw', minMax: 0.15 },
    { name: 'RMSE', key: 'rmse_kw', minMax: 0.25 },
    { name: '日间 nRMSE', key: 'daytime_nrmse_capacity', minMax: 0.25 },
    { name: '日间 MAPE', key: 'daytime_mape', minMax: 1.5 },
  ]
  const indicator = metricDefs.map(metric => {
    const observedMax = Math.max(...unique.map(row => Number(row[metric.key])).filter(Number.isFinite), 0)
    return {
      name: metric.name,
      max: Math.max(metric.minMax, observedMax * 1.2 || metric.minMax),
    }
  })
  const seriesName = row => `${shortModel(row.model)} ${shortTarget(row.target)}`

  return {
    tooltip: {
      trigger: 'item',
      formatter(params) {
        const row = unique.find(item => seriesName(item) === params.seriesName)
        if (!row) return ''
        return [
          `<strong>${seriesName(row)}</strong>`,
          `nRMSE：${fmtNum(row.nrmse_capacity)}`,
          `MAE：${fmtNum(row.mae_kw)}`,
          `RMSE：${fmtNum(row.rmse_kw)}`,
          `日间 nRMSE：${fmtNum(row.daytime_nrmse_capacity)}`,
          `日间 MAPE：${fmtNum(row.daytime_mape)}`,
        ].join('<br/>')
      },
    },
    legend: {
      data: unique.map(row => seriesName(row)),
      top: 0,
      textStyle: { color: '#606266', fontSize: 11 },
    },
    radar: {
      indicator,
      center: ['50%', '55%'],
      radius: '60%',
      axisName: { color: '#606266' },
      splitLine: { lineStyle: { color: '#dcdfe6' } },
      splitArea: { areaStyle: { color: ['rgba(64,158,255,0.03)', 'rgba(64,158,255,0.06)'] } },
    },
    series: unique.map(row => ({
      name: seriesName(row),
      type: 'radar',
      data: [
        {
          name: seriesName(row),
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
      itemStyle: { color: modelColor(row.model), borderColor: '#ffffff', borderWidth: 1 },
      areaStyle: { color: modelColor(row.model), opacity: 0.1 },
    })),
  }
}
