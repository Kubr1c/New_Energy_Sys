export function buildParetoOption(sensitivity) {
  if (!sensitivity.length) return {}

  const pareto = sensitivity.filter(row => row.pareto_front === true || row.pareto_front === 'True')
  const nonPareto = sensitivity.filter(row => row.pareto_front !== true && row.pareto_front !== 'True')

  return {
    tooltip: {
      trigger: 'item',
      formatter: point => `${point.data[3]}<br/>Revenue: €${Number(point.data[0]).toFixed(3)}<br/>Cycles: ${Number(point.data[1]).toFixed(0)}<br/>Shortfall: ${Number(point.data[2]).toFixed(1)} kWh`,
    },
    grid: { left: 66, right: 36, top: 36, bottom: 58 },
    xAxis: { type: 'value', name: 'Incremental Revenue (€)', nameLocation: 'center', nameGap: 34 },
    yAxis: { type: 'value', name: 'Cycle Count' },
    series: [
      {
        name: 'Pareto Front',
        type: 'scatter',
        data: pareto.map(row => [
          Number(row.incremental_revenue_eur),
          Number(row.cycle_equivalent_count),
          Number(row.total_shortfall_kwh),
          row.config_id,
        ]),
        symbolSize: 14,
        itemStyle: { color: '#00f5a0', shadowBlur: 8, shadowColor: 'rgba(0,245,160,0.35)' },
      },
      {
        name: 'Non-Pareto',
        type: 'scatter',
        data: nonPareto.map(row => [
          Number(row.incremental_revenue_eur),
          Number(row.cycle_equivalent_count),
          Number(row.total_shortfall_kwh),
          row.config_id,
        ]),
        symbolSize: 8,
        itemStyle: { color: 'rgba(255,255,255,0.32)' },
      },
    ],
  }
}

export function buildRevenueHeatmapOption(sensitivity) {
  if (!sensitivity.length) return {}

  const capSet = [...new Set(sensitivity.map(row => Number(row.capacity_multiplier)))].sort()
  const powSet = [...new Set(sensitivity.map(row => Number(row.power_multiplier)))].sort()
  const dataMap = {}

  sensitivity.forEach(row => {
    const key = `${row.capacity_multiplier}_${row.power_multiplier}`
    if (!dataMap[key]) dataMap[key] = []
    dataMap[key].push(Number(row.incremental_revenue_eur))
  })

  const heatData = []
  capSet.forEach((capacity, capacityIndex) => {
    powSet.forEach((power, powerIndex) => {
      const values = dataMap[`${capacity}_${power}`] || [0]
      const avg = values.reduce((sum, value) => sum + value, 0) / values.length
      heatData.push([capacityIndex, powerIndex, Number(avg.toFixed(4))])
    })
  })

  return {
    tooltip: {
      formatter: point => `Cap x${capSet[point.data[0]]}, Pow x${powSet[point.data[1]]}<br/>Avg Incr. Revenue: €${point.data[2]}`,
    },
    grid: { left: 86, right: 86, top: 28, bottom: 44 },
    xAxis: { type: 'category', data: capSet.map(capacity => `x${capacity}`), name: 'Capacity Multiplier' },
    yAxis: { type: 'category', data: powSet.map(power => `x${power}`), name: 'Power Multiplier' },
    visualMap: {
      min: -0.2,
      max: 2,
      calculable: true,
      orient: 'vertical',
      right: 10,
      top: 'center',
      inRange: { color: ['#1a237e', '#0d47a1', '#00838f', '#00c853', '#ffd600'] },
      textStyle: { color: 'rgba(255,255,255,0.72)' },
    },
    series: [
      {
        type: 'heatmap',
        data: heatData,
        label: { show: true, color: '#fff', fontSize: 11, formatter: point => `€${point.data[2]}` },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,212,255,0.5)' } },
      },
    ],
  }
}
