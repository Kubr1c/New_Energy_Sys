import { eurToCny, formatYuan } from '../utils/currency'

function formatConfigCombo(row) {
  const cap = Number(row.capacity_multiplier)
  const pow = Number(row.power_multiplier)
  const parts = []
  if (Number.isFinite(cap)) parts.push(`容量 x${cap.toFixed(1)}`)
  if (Number.isFinite(pow)) parts.push(`功率 x${pow.toFixed(1)}`)
  return parts.length ? parts.join(' / ') : '配置组合'
}

export function buildParetoOption(sensitivity) {
  if (!sensitivity.length) return {}

  const pareto = sensitivity.filter(row => row.pareto_front === true || row.pareto_front === 'True')
  const nonPareto = sensitivity.filter(row => row.pareto_front !== true && row.pareto_front !== 'True')

  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#dcdfe6',
      borderWidth: 1,
      textStyle: { color: '#303133' },
      extraCssText: 'box-shadow: 0 10px 28px rgba(15, 23, 42, 0.14);',
      formatter: point => `配置组合: ${point.data[3]}<br/>相比无储能收益: ${formatYuan(point.data[0], 2)}<br/>循环次数: ${Number(point.data[1]).toFixed(0)}<br/>缺口电量: ${Number(point.data[2]).toFixed(1)} kWh`,
    },
    grid: { left: 66, right: 36, top: 36, bottom: 58 },
    xAxis: { type: 'value', name: '相比无储能收益（元）', nameLocation: 'center', nameGap: 34 },
    yAxis: { type: 'value', name: '循环次数' },
    series: [
      {
        name: '较优组合',
        type: 'scatter',
        data: pareto.map(row => [
          eurToCny(row.incremental_revenue_eur),
          Number(row.cycle_equivalent_count),
          Number(row.total_shortfall_kwh),
          formatConfigCombo(row),
        ]),
        symbolSize: 14,
        itemStyle: { color: '#16a34a', shadowBlur: 8, shadowColor: 'rgba(22,163,74,0.22)' },
      },
      {
        name: '其他组合',
        type: 'scatter',
        data: nonPareto.map(row => [
          eurToCny(row.incremental_revenue_eur),
          Number(row.cycle_equivalent_count),
          Number(row.total_shortfall_kwh),
          formatConfigCombo(row),
        ]),
        symbolSize: 8,
        itemStyle: { color: '#94a3b8' },
      },
    ],
  }
}

export function buildRevenueHeatmapOption(sensitivity) {
  if (!sensitivity.length) return {}

  const capSet = [...new Set(sensitivity.map(row => Number(row.capacity_multiplier)))].sort((a, b) => a - b)
  const powSet = [...new Set(sensitivity.map(row => Number(row.power_multiplier)))].sort((a, b) => a - b)
  const dataMap = {}

  sensitivity.forEach(row => {
    const key = `${row.capacity_multiplier}_${row.power_multiplier}`
    if (!dataMap[key]) dataMap[key] = []
    dataMap[key].push(eurToCny(row.incremental_revenue_eur))
  })

  const heatData = []
  capSet.forEach((capacity, capacityIndex) => {
    powSet.forEach((power, powerIndex) => {
      const values = dataMap[`${capacity}_${power}`] || [0]
      const avg = values.reduce((sum, value) => sum + value, 0) / values.length
      heatData.push([capacityIndex, powerIndex, Number(avg.toFixed(4))])
    })
  })
  const heatValues = heatData.map(item => Number(item[2])).filter(Number.isFinite)
  const minValue = heatValues.length ? Math.min(...heatValues) : 0
  const maxValue = heatValues.length ? Math.max(...heatValues) : 1
  const spread = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.08)

  return {
    tooltip: {
      formatter: point => `容量 x${capSet[point.data[0]]}, 功率 x${powSet[point.data[1]]}<br/>平均收益变化: ${formatYuan(point.data[2], 2)}`,
    },
    grid: { left: 86, right: 86, top: 28, bottom: 44 },
    xAxis: { type: 'category', data: capSet.map(capacity => `x${capacity}`), name: '容量倍率' },
    yAxis: { type: 'category', data: powSet.map(power => `x${power}`), name: '功率倍率' },
    visualMap: {
      min: minValue - spread * 0.08,
      max: maxValue + spread * 0.08,
      calculable: true,
      orient: 'vertical',
      right: 10,
      top: 'center',
      inRange: { color: ['#dbeafe', '#93c5fd', '#38bdf8', '#22c55e', '#f59e0b'] },
      textStyle: { color: '#64748b' },
    },
    series: [
      {
        type: 'heatmap',
        data: heatData,
        label: { show: true, color: '#0f172a', fontSize: 11, formatter: point => formatYuan(point.data[2], 2) },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,212,255,0.5)' } },
      },
    ],
  }
}
