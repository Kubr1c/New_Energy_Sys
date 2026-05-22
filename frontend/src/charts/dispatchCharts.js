import { chartColors } from './chartTheme'
import { scenarioLabel } from '../utils/displayLabels'
import { eurToCny, formatYuan, formatYuanFromEur, formatYuanPerMwh } from '../utils/currency'
import { formatReferenceSiteHour } from '../utils/siteTime'

function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function money(value) {
  return formatYuan(value)
}

function moneyFromEur(value) {
  return formatYuanFromEur(value)
}

function formatHourLabel(value) {
  return formatReferenceSiteHour(value, String(value))
}

function modeLabel(mode) {
  return scenarioLabel(mode)
}

export function buildScoreBarOption(scorecard) {
  if (!scorecard.length) return {}

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = scorecard[index] || {}
        return [
          `<strong>${scenarioLabel(row.scenario_id)}</strong>`,
          `经济性评分：${toNumber(row.economic_score).toFixed(1)}，表示收益表现。`,
          `约束满足评分：${toNumber(row.constraint_score).toFixed(1)}，表示SOC等约束满足程度。`,
          `风险控制评分：${toNumber(row.risk_score).toFixed(1)}，分值越高表示风险越可控。`,
        ].join('<br/>')
      },
    },
    legend: { data: ['经济性', '约束满足', '风险控制'], top: 0, textStyle: { color: '#606266' } },
    grid: { left: 150, right: 30, top: 44, bottom: 24 },
    xAxis: { type: 'value', max: 100, axisLabel: { color: '#606266' } },
    yAxis: {
      type: 'category',
      data: scorecard.map(item => scenarioLabel(item.scenario_id)),
      axisLabel: { fontSize: 11, color: '#606266' },
    },
    series: [
      { name: '经济性', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.economic_score) / 3).toFixed(1)), itemStyle: { color: chartColors.cyan } },
      { name: '约束满足', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.constraint_score) / 3).toFixed(1)), itemStyle: { color: chartColors.green } },
      { name: '风险控制', type: 'bar', stack: 'score', data: scorecard.map(item => (Number(item.risk_score) / 3).toFixed(1)), itemStyle: { color: chartColors.orange } },
    ],
  }
}

export function buildGovernanceRadarOption(scorecard) {
  if (!scorecard.length) return {}

  const rows = scorecard.slice(0, 4)
  const colors = [chartColors.cyan, chartColors.green, chartColors.orange, chartColors.red]

  return {
    legend: { data: rows.map(item => scenarioLabel(item.scenario_id)), top: 0, textStyle: { fontSize: 10, color: '#606266' } },
    radar: {
      indicator: [
        { name: '经济性', max: 100 },
        { name: '约束满足', max: 100 },
        { name: '风险控制', max: 100 },
      ],
      center: ['50%', '56%'],
      radius: '58%',
      axisName: { color: '#606266' },
    },
    series: [
      {
        type: 'radar',
        data: rows.map((item, index) => ({
          name: scenarioLabel(item.scenario_id),
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

  const order = ['no_storage', 'stage10_fixed_threshold', 'stage11_best_threshold_q40_q95', 'rolling_optimization']
  const rows = order.map(id => metrics.find(item => item.scenario === id)).filter(Boolean)

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const item = params?.[0]
        const row = rows[item?.dataIndex]
        if (!row) return ''
        return `${scenarioLabel(row.scenario)}<br/>相对无储能基准的增量收益：${moneyFromEur(row.incremental_revenue_eur || 0)}`
      },
    },
    grid: { left: 86, right: 30, top: 28, bottom: 76 },
    xAxis: {
      type: 'category',
      data: rows.map(item => scenarioLabel(item.scenario)),
      axisLabel: { interval: 0, rotate: 16, fontSize: 11, color: '#606266' },
    },
    yAxis: {
      type: 'value',
      name: '仿真收益（元）',
      axisLabel: { formatter: value => formatYuan(value, 0), color: '#606266' },
      nameTextStyle: { color: '#606266' },
    },
    series: [
      {
        name: '相对无储能基准的增量收益',
        type: 'bar',
        data: rows.map(item => eurToCny(item.incremental_revenue_eur || 0).toFixed(2)),
        itemStyle: {
          color: params => {
            const value = Number(rows[params.dataIndex]?.incremental_revenue_eur || 0)
            if (value < 0) return chartColors.red
            if (rows[params.dataIndex]?.scenario === 'stage11_best_threshold_q40_q95') return chartColors.orange
            return chartColors.cyan
          },
        },
        label: { show: true, position: 'top', formatter: params => formatYuan(params.value, 0), fontSize: 10, color: '#303133' },
      },
    ],
  }
}

export function buildStage21WeatherPriceOption(weatherRows, priceRows, scenarioId) {
  if (!weatherRows.length || !priceRows.length || !scenarioId) return {}

  const pricesByTime = new Map(
    priceRows
      .filter(item => item.price_scenario_id === scenarioId)
      .map(item => [String(item.timestamp), toNumber(item.price_eur_mwh)]),
  )
  const rows = weatherRows
    .map(item => ({
      time: item.weather_valid_time || item.timestamp,
      pv: toNumber(item.weather_estimated_pv_kw ?? item.prediction_kw),
      ghi: toNumber(item.ghi_wm2),
      price: pricesByTime.get(String(item.weather_valid_time || item.timestamp)),
    }))
    .filter(item => item.price !== undefined)

  if (!rows.length) return {}

  const labels = rows.map(item => formatHourLabel(item.time))

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        const lines = params.map(item => {
          if (item.seriesName === '电价') return `${item.marker}${item.seriesName}: ${formatYuanPerMwh(item.value, 1)}`
          const unit = item.seriesName === '太阳辐照强度' ? ' W/m²' : ' kW'
          return `${item.marker}${item.seriesName}: ${Number(item.value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${unit}`
        })
        return [`${formatHourLabel(row.time)}`, ...lines].join('<br/>')
      },
    },
    legend: { data: ['天气估算功率', '太阳辐照强度', '电价'], top: 0, textStyle: { color: '#606266' } },
    grid: [
      { left: 76, right: 36, top: 44, height: '24%' },
      { left: 76, right: 36, top: '39%', height: '21%' },
      { left: 76, right: 36, top: '69%', height: '19%', bottom: 54 },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: labels, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', gridIndex: 1, data: labels, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', gridIndex: 2, data: labels, axisLabel: { interval: 3, rotate: 18, fontSize: 10, color: '#606266' } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: '光伏功率 (kW)', axisLabel: { formatter: value => Number(value).toFixed(0), color: '#606266' }, nameTextStyle: { color: '#606266' } },
      { type: 'value', gridIndex: 1, name: '辐照度 (W/m²)', axisLabel: { formatter: value => Number(value).toFixed(0), color: '#606266' }, nameTextStyle: { color: '#606266' } },
      { type: 'value', gridIndex: 2, name: '电价 (元/MWh)', axisLabel: { formatter: value => formatYuanPerMwh(value, 0), color: '#606266' }, nameTextStyle: { color: '#606266' } },
    ],
    series: [
      { name: '天气估算功率', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none', data: rows.map(item => Number(item.pv.toFixed(2))), itemStyle: { color: chartColors.cyan } },
      { name: '太阳辐照强度', type: 'line', xAxisIndex: 1, yAxisIndex: 1, smooth: true, symbol: 'none', data: rows.map(item => Number(item.ghi.toFixed(2))), itemStyle: { color: chartColors.orange } },
      { name: '电价', type: 'line', xAxisIndex: 2, yAxisIndex: 2, smooth: true, symbol: 'none', data: rows.map(item => Number(eurToCny(item.price).toFixed(2))), itemStyle: { color: chartColors.green } },
    ],
  }
}

function filterRollingRows(rows, scenarioId, dispatchMode) {
  const byScenario = rows.filter(item => item.price_scenario_id === scenarioId && item.scenario === 'rolling_optimization')
  const byMode = byScenario.filter(item => item.dispatch_mode === dispatchMode)
  return byMode.length ? byMode : byScenario.filter(item => !item.dispatch_mode)
}

export function buildStage21SocPowerOption(results, scenarioId, dispatchMode = 'smooth') {
  if (!results.length || !scenarioId) return {}

  const rows = filterRollingRows(results, scenarioId, dispatchMode)
    .map(item => ({
      time: item.dispatch_timestamp || item.timestamp,
      soc: toNumber(item.soc_end) * 100,
      charge: toNumber(item.actual_charge_kw),
      discharge: -toNumber(item.actual_discharge_kw),
    }))
  if (!rows.length) return {}

  const labels = rows.map(item => formatHourLabel(item.time))

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        const lines = params.map(item => {
          const unit = item.seriesName === 'SOC' ? '%' : ' kW'
          const value = item.seriesName === 'SOC'
            ? Number(item.value).toFixed(1)
            : Number(item.value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
          return `${item.marker}${item.seriesName}: ${value}${unit}`
        })
        return [`${formatHourLabel(row.time)}`, ...lines].join('<br/>')
      },
    },
    legend: { data: ['SOC', '充电功率', '放电功率'], top: 0, textStyle: { color: '#606266' } },
    title: { text: modeLabel(dispatchMode), left: 8, top: 4, textStyle: { color: '#606266', fontSize: 12, fontWeight: 500 } },
    grid: [
      { left: 70, right: 42, top: 48, height: '34%' },
      { left: 70, right: 42, top: '58%', height: '30%', bottom: 54 },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: labels, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', gridIndex: 1, data: labels, axisLabel: { interval: 3, rotate: 18, fontSize: 10, color: '#606266' } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: 'SOC %', min: 0, max: 100, axisLabel: { formatter: value => `${Number(value).toFixed(0)}%`, color: '#606266' }, nameTextStyle: { color: '#606266' } },
      { type: 'value', gridIndex: 1, name: '储能功率 (kW)', axisLabel: { formatter: value => Number(value).toFixed(0), color: '#606266' }, nameTextStyle: { color: '#606266' } },
    ],
    series: [
      { name: 'SOC', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none', data: rows.map(item => Number(item.soc.toFixed(2))), itemStyle: { color: chartColors.green } },
      { name: '充电功率', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, stack: 'power', data: rows.map(item => Number(item.charge.toFixed(2))), itemStyle: { color: chartColors.cyan } },
      { name: '放电功率', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, stack: 'power', data: rows.map(item => Number(item.discharge.toFixed(2))), itemStyle: { color: chartColors.orange } },
    ],
  }
}

export function buildStage21ScenarioRevenueOption(metrics) {
  const rows = metrics.filter(item => item.scenario === 'rolling_optimization')
  if (!rows.length) return {}
  const scenarios = Array.from(new Map(rows.map(item => [
    item.price_scenario_id,
    scenarioLabel(item.price_scenario_label || item.price_scenario_id),
  ])).entries())
  const modes = ['smooth', 'economic'].filter(mode => rows.some(item => item.dispatch_mode === mode))
  const visibleModes = modes.length ? modes : ['legacy']

  return {
    tooltip: { trigger: 'axis', valueFormatter: value => money(value) },
    legend: { data: visibleModes.map(mode => (mode === 'legacy' ? '滚动优化调度方案' : modeLabel(mode))), top: 0, textStyle: { color: '#606266' } },
    grid: { left: 86, right: 26, top: 44, bottom: 78 },
    xAxis: {
      type: 'category',
      data: scenarios.map(([, label]) => label),
      axisLabel: { interval: 0, rotate: 22, fontSize: 10, color: '#606266' },
    },
    yAxis: { type: 'value', name: '仿真收益（元）', axisLabel: { formatter: value => formatYuan(value, 0), color: '#606266' } },
    series: visibleModes.map(mode => {
      const modeRows = scenarios.map(([scenarioId]) => {
        const row = mode === 'legacy'
          ? rows.find(item => item.price_scenario_id === scenarioId)
          : rows.find(item => item.price_scenario_id === scenarioId && item.dispatch_mode === mode)
        return row || {}
      })
      return {
        name: mode === 'legacy' ? '滚动优化调度方案' : modeLabel(mode),
        type: 'bar',
        data: modeRows.map(item => eurToCny(item.incremental_revenue_eur).toFixed(2)),
        itemStyle: { color: params => (toNumber(modeRows[params.dataIndex]?.incremental_revenue_eur) >= 0 ? (mode === 'economic' ? chartColors.orange : chartColors.cyan) : chartColors.red) },
        label: { show: true, position: 'top', formatter: params => formatYuan(params.value, 0), fontSize: 10, color: '#303133' },
      }
    }),
  }
}

function selectedMarkLine(rows, selectedIndex) {
  const row = rows[selectedIndex]
  if (!row) return undefined
  return {
    silent: true,
    symbol: 'none',
    lineStyle: { color: '#c0c4cc', type: 'dashed' },
    label: { show: false },
    data: [{ xAxis: formatHourLabel(row.time) }],
  }
}

function baseExperimentAxis(rows, selectedIndex, right = 36) {
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 70, right, top: 44, bottom: 64 },
    xAxis: {
      type: 'category',
      data: rows.map(item => formatHourLabel(item.time)),
      axisLabel: { interval: rows.length > 48 ? 7 : rows.length > 24 ? 5 : 3, rotate: 18, fontSize: 10, color: '#606266' },
    },
    selectedLine: selectedMarkLine(rows, selectedIndex),
  }
}

export function buildExperimentWeatherTrendOption(rows, selectedIndex = 0) {
  if (!rows.length) return {}
  const base = baseExperimentAxis(rows, selectedIndex, 70)
  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        const lines = params.map(item => `${item.marker}${item.seriesName}: ${item.value}${item.seriesName === '辐照度' ? ' W/m2' : item.seriesName === '温度' ? ' °C' : '%'}`)
        lines.push(`风速: ${toNumber(row.windSpeedMs).toFixed(1)} m/s`)
        lines.push(`湿度: ${toNumber(row.humidityPct).toFixed(0)}%`)
        return [`${formatHourLabel(row.time)}`, ...lines].join('<br/>')
      },
    },
    legend: { data: ['辐照度', '云量', '温度'], top: 0, textStyle: { color: '#606266' } },
    grid: base.grid,
    xAxis: base.xAxis,
    yAxis: [
      { type: 'value', name: 'W/m2', axisLabel: { color: '#606266' } },
      { type: 'value', name: '°C / %', axisLabel: { color: '#606266' } },
    ],
    series: [
      { name: '辐照度', type: 'line', smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.ghiWm2).toFixed(1)), itemStyle: { color: chartColors.yellow }, markLine: base.selectedLine },
      { name: '云量', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.cloudCoverPct).toFixed(1)), itemStyle: { color: chartColors.blue } },
      { name: '温度', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.temperatureC).toFixed(1)), itemStyle: { color: chartColors.orange } },
    ],
  }
}

export function buildExperimentPvForecastOption(rows, selectedIndex = 0) {
  if (!rows.length) return {}
  const base = baseExperimentAxis(rows, selectedIndex, 74)
  return {
    tooltip: base.tooltip,
    legend: { data: ['光伏预测功率', '辐照度'], top: 0, textStyle: { color: '#606266' } },
    grid: { ...base.grid, bottom: 72 },
    xAxis: base.xAxis,
    yAxis: [
      { type: 'value', name: 'kW', axisLabel: { color: '#606266' } },
      { type: 'value', name: 'W/m2', axisLabel: { color: '#606266' } },
    ],
    series: [
      { name: '光伏预测功率', type: 'line', smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.pvKw).toFixed(2)), itemStyle: { color: chartColors.cyan }, markLine: base.selectedLine },
      { name: '辐照度', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.ghiWm2).toFixed(2)), itemStyle: { color: chartColors.yellow } },
    ],
  }
}

export function buildExperimentPowerDispatchOption(rows, selectedIndex = 0) {
  if (!rows.length) return {}
  const labels = rows.map(item => formatHourLabel(item.time))
  const selectedLine = selectedMarkLine(rows, selectedIndex)
  const xAxisLabel = { interval: rows.length > 48 ? 7 : rows.length > 24 ? 5 : 3, rotate: 18, fontSize: 10, color: '#606266' }
  const storageNetValues = rows.map(item => toNumber(item.dischargeKw) - toNumber(item.chargeKw))
  const storageMax = Math.max(...rows.flatMap(item => [
    Math.abs(toNumber(item.chargeKw)),
    Math.abs(toNumber(item.dischargeKw)),
    Math.abs(toNumber(item.dischargeKw) - toNumber(item.chargeKw)),
  ]), 1)
  const storageAxisMax = Math.ceil(storageMax * 1.15)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', link: [{ xAxisIndex: [0, 1] }] },
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        const pvKw = toNumber(row.pvKw)
        const chargeKw = toNumber(row.chargeKw)
        const dischargeKw = toNumber(row.dischargeKw)
        const netKw = dischargeKw - chargeKw
        const gridKw = toNumber(row.gridKw)
        return [
          `${formatHourLabel(row.time)}`,
          `光伏预测功率：${pvKw.toFixed(2)} kW`,
          `储能充电功率：${chargeKw.toFixed(2)} kW`,
          `储能放电功率：${dischargeKw.toFixed(2)} kW`,
          `储能净功率：${netKw.toFixed(2)} kW`,
          `调度后并网功率：${gridKw.toFixed(2)} kW`,
          '并网功率 = 光伏出力 - 充电功率 + 放电功率，并受并网约束影响。',
        ].join('<br/>')
      },
    },
    legend: { data: ['光伏预测功率', '调度后并网功率', '储能净功率'], top: 0, textStyle: { color: '#606266' } },
    grid: [
      { left: 70, right: 36, top: 48, height: '42%' },
      { left: 70, right: 36, top: '62%', height: '24%' },
    ],
    xAxis: [
      {
        type: 'category',
        gridIndex: 0,
        data: labels,
        axisLabel: { show: false },
        axisTick: { show: false },
      },
      {
        type: 'category',
        gridIndex: 1,
        data: labels,
        axisLabel: xAxisLabel,
      },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: '光伏/并网 kW', axisLabel: { color: '#606266' }, splitLine: { lineStyle: { color: '#ebeef5' } } },
      { type: 'value', gridIndex: 1, name: '储能净功率 kW', min: -storageAxisMax, max: storageAxisMax, axisLabel: { color: '#606266' }, splitLine: { lineStyle: { color: '#ebeef5' } } },
    ],
    series: [
      { name: '光伏预测功率', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none', data: rows.map(item => Number(toNumber(item.pvKw).toFixed(2))), itemStyle: { color: chartColors.cyan }, markLine: selectedLine },
      { name: '调度后并网功率', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none', data: rows.map(item => Number(toNumber(item.gridKw).toFixed(2))), itemStyle: { color: chartColors.green } },
      { name: '储能净功率', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: storageNetValues.map(value => Number(value.toFixed(2))), itemStyle: { color: params => Number(params.value) >= 0 ? chartColors.orange : chartColors.blue }, markLine: selectedLine },
    ],
  }
}

export function buildExperimentSocOption(rows, selectedIndex = 0) {
  if (!rows.length) return {}
  const base = baseExperimentAxis(rows, selectedIndex)
  return {
    tooltip: base.tooltip,
    legend: { data: ['SOC', 'SOC下限', 'SOC上限'], top: 0, textStyle: { color: '#606266' } },
    grid: base.grid,
    xAxis: base.xAxis,
    yAxis: { type: 'value', name: 'SOC %', min: 0, max: 100, axisLabel: { formatter: value => `${Number(value).toFixed(0)}%`, color: '#606266' } },
    series: [
      { name: 'SOC', type: 'line', smooth: true, symbol: 'none', data: rows.map(item => toNumber(item.socPct).toFixed(2)), itemStyle: { color: chartColors.green }, markLine: base.selectedLine },
      { name: 'SOC下限', type: 'line', symbol: 'none', data: rows.map(item => toNumber(item.socMinPct).toFixed(2)), lineStyle: { color: chartColors.red, type: 'dashed' } },
      { name: 'SOC上限', type: 'line', symbol: 'none', data: rows.map(item => toNumber(item.socMaxPct).toFixed(2)), lineStyle: { color: chartColors.cyan, type: 'dashed' } },
    ],
  }
}

export function buildExperimentPriceRevenueOption(rows, selectedIndex = 0) {
  if (!rows.length) return {}
  const base = baseExperimentAxis(rows, selectedIndex, 70)
  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const index = params?.[0]?.dataIndex ?? 0
        const row = rows[index] || {}
        const lines = params.map(item => {
          if (item.seriesName === '电价') return `${item.marker}${item.seriesName}: ${formatYuanPerMwh(item.value, 1)}`
          return `${item.marker}${item.seriesName}: ${formatYuan(item.value)}`
        })
        return [`${formatHourLabel(row.time)}`, ...lines].join('<br/>')
      },
    },
    legend: { data: ['电价', '小时收益', '增量收益'], top: 0, textStyle: { color: '#606266' } },
    grid: base.grid,
    xAxis: base.xAxis,
    yAxis: [
      { type: 'value', name: '仿真收益（元）', axisLabel: { formatter: value => formatYuan(value, 0), color: '#606266' } },
      { type: 'value', name: '代理电价（元/MWh）', axisLabel: { formatter: value => formatYuanPerMwh(value, 0), color: '#606266' } },
    ],
    series: [
      { name: '小时收益', type: 'bar', data: rows.map(item => eurToCny(item.revenueEur).toFixed(2)), itemStyle: { color: chartColors.green }, markLine: base.selectedLine },
      { name: '增量收益', type: 'bar', data: rows.map(item => eurToCny(item.incrementalRevenueEur).toFixed(2)), itemStyle: { color: params => (Number(params.value) >= 0 ? chartColors.cyan : chartColors.red) } },
      { name: '电价', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', data: rows.map(item => eurToCny(item.priceEurMwh).toFixed(2)), itemStyle: { color: chartColors.orange } },
    ],
  }
}

export function buildExperimentComparisonOption(rows) {
  if (!rows.length) return {}
  return {
    tooltip: { trigger: 'axis', valueFormatter: value => money(value) },
    legend: { data: ['增量收益'], top: 0, textStyle: { color: '#606266' } },
    grid: { left: 82, right: 30, top: 44, bottom: 74 },
    xAxis: {
      type: 'category',
      data: rows.map(item => item.label),
      axisLabel: { interval: 0, rotate: 14, fontSize: 10, color: '#606266' },
    },
    yAxis: { type: 'value', name: '相比无储能收益（元）', axisLabel: { formatter: value => formatYuan(value, 0), color: '#606266' } },
    series: [
      { name: '增量收益', type: 'bar', data: rows.map(item => eurToCny(item.incrementalRevenueEur).toFixed(2)), itemStyle: { color: params => (Number(params.value) >= 0 ? chartColors.cyan : chartColors.red) } },
    ],
  }
}

export function buildExperimentSensitivityOption(rows) {
  if (!rows.length) return {}
  return {
    tooltip: { trigger: 'axis', valueFormatter: value => money(value) },
    legend: { data: ['容量敏感性', '功率敏感性'], top: 0, textStyle: { color: '#606266' } },
    grid: { left: 82, right: 30, top: 44, bottom: 58 },
    xAxis: {
      type: 'category',
      data: rows.map(item => item.label),
      axisLabel: { color: '#606266' },
    },
    yAxis: { type: 'value', name: '仿真收益（元）', axisLabel: { formatter: value => formatYuan(value, 0), color: '#606266' } },
    series: [
      { name: '容量敏感性', type: 'line', smooth: true, data: rows.map(item => eurToCny(item.capacityRevenueEur).toFixed(2)), itemStyle: { color: chartColors.cyan } },
      { name: '功率敏感性', type: 'line', smooth: true, data: rows.map(item => eurToCny(item.powerRevenueEur).toFixed(2)), itemStyle: { color: chartColors.orange } },
    ],
  }
}
