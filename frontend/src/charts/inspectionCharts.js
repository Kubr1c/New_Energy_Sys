import { scenarioLabel } from '../utils/displayLabels'

const HORIZON_COLORS = {
  1: '#2ee88b',
  6: '#38bdf8',
  24: '#ff7a7a',
}

const HORIZON_LABELS = {
  1: 't+1h',
  6: 't+6h',
  24: 't+24h',
}

const SERIES_LABELS = {
  actualPower: '实际功率',
  actualEnergy: '实际发电量',
  persistence: '持续性基准',
  prediction: '预测值',
  errorPower: '预测误差',
  errorEnergy: '发电量误差',
}

const SCENARIO_STYLES = {
  clear: {
    label: '晴天',
    color: '#ffe45c',
    area: 'rgba(255, 228, 92, 0.13)',
    border: 'rgba(255, 228, 92, 0.34)',
    lineType: 'solid',
  },
  mixed: {
    label: '多云',
    color: '#35d3ff',
    area: 'rgba(53, 211, 255, 0.12)',
    border: 'rgba(53, 211, 255, 0.34)',
    lineType: 'dashed',
  },
  overcast: {
    label: '阴天',
    color: '#f472ff',
    area: 'rgba(244, 114, 255, 0.12)',
    border: 'rgba(244, 114, 255, 0.34)',
    lineType: [8, 4, 2, 4],
  },
  all: {
    label: '全部场景',
    color: '#ffffff',
    area: 'rgba(255, 255, 255, 0.08)',
    border: 'rgba(255, 255, 255, 0.22)',
    lineType: 'solid',
  },
}

const AXIS_TEXT_STYLE = { fontSize: 11, color: 'rgba(255,255,255,0.78)' }
const TOOLTIP_STYLE = {
  backgroundColor: 'rgba(10,14,39,0.95)',
  borderColor: 'rgba(0,212,255,0.32)',
  borderWidth: 1,
  textStyle: { color: 'rgba(255,255,255,0.92)', fontSize: 12 },
  confine: true,
}

function horizonLabel(horizon) {
  return HORIZON_LABELS[horizon] || `t+${horizon}h`
}

function normalizeScenario(value) {
  const key = String(value || '').toLowerCase()
  if (key.includes('clear')) return 'clear'
  if (key.includes('mixed') || key.includes('cloud')) return 'mixed'
  if (key.includes('overcast')) return 'overcast'
  return key || 'all'
}

function compactScenarioLabel(value) {
  const normalized = normalizeScenario(value)
  return SCENARIO_STYLES[normalized]?.label || scenarioLabel(value)
}

function scenarioStyle(value) {
  return SCENARIO_STYLES[normalizeScenario(value)] || SCENARIO_STYLES.all
}

function toDateKey(value) {
  if (!value) return ''
  return String(value).slice(0, 10)
}

function formatNumber(value, digits = 3) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '-'
}

function shortDate(value) {
  return String(value || '').slice(5, 10)
}

function emptyOption(title = '暂无数据') {
  return {
    title: {
      text: title,
      left: 'center',
      top: 'center',
      textStyle: { color: 'rgba(255,255,255,0.72)', fontSize: 14 },
    },
    grid: [],
    xAxis: [],
    yAxis: [],
    series: [],
  }
}

function buildNightMarkAreas(timePoints, solarElevations) {
  const marks = []
  let startIdx = -1

  for (let i = 0; i < solarElevations.length; i += 1) {
    const isNight = Number(solarElevations[i] ?? 90) <= 5
    if (isNight && startIdx === -1) startIdx = i
    if (!isNight && startIdx !== -1) {
      marks.push([timePoints[startIdx], timePoints[i - 1]])
      startIdx = -1
    }
  }

  if (startIdx !== -1) marks.push([timePoints[startIdx], timePoints[timePoints.length - 1]])
  return marks
}

function buildScenarioMarkAreas(timePoints, solarElevations, scenarios, scenarioMode) {
  const marks = []
  let startIdx = -1
  let currentScenario = null

  for (let i = 0; i < timePoints.length; i += 1) {
    const isDaytime = Number(solarElevations[i] ?? 90) > 5
    const scenario = isDaytime ? normalizeScenario(scenarios[i]) : null
    if (isDaytime && scenario) {
      if (startIdx === -1) {
        startIdx = i
        currentScenario = scenario
      } else if (scenario !== currentScenario) {
        marks.push({ start: [timePoints[startIdx], timePoints[i - 1]], scenario: currentScenario })
        startIdx = i
        currentScenario = scenario
      }
    } else if (startIdx !== -1) {
      marks.push({ start: [timePoints[startIdx], timePoints[i - 1]], scenario: currentScenario })
      startIdx = -1
      currentScenario = null
    }
  }

  if (startIdx !== -1) marks.push({ start: [timePoints[startIdx], timePoints[timePoints.length - 1]], scenario: currentScenario })
  const normalizedMode = normalizeScenario(scenarioMode)
  return normalizedMode && normalizedMode !== 'all' ? marks.filter(item => item.scenario === normalizedMode) : marks
}

function formatZoomLabel(value, timePoints) {
  const category = typeof value === 'number'
    ? timePoints[Math.max(0, Math.min(timePoints.length - 1, Math.round(value)))]
    : value
  return typeof category === 'string' ? category.slice(5, 16) : ''
}

function findRow(rows, time, horizon, experiment) {
  return rows.find(row => row.valid_time === time && Number(row.horizon_hours) === Number(horizon) && row.experiment === experiment)
}

function groupHourlyData(rawData, experiment, horizons) {
  const sorted = [...rawData]
    .filter(row => row.valid_time)
    .sort((a, b) => new Date(a.valid_time) - new Date(b.valid_time))
  const timePoints = [...new Set(sorted.map(row => row.valid_time))].sort((a, b) => new Date(a) - new Date(b))

  const actualMap = new Map()
  const persistenceMap = new Map()
  const elevationMap = new Map()
  const scenarioMap = new Map()

  sorted.forEach(row => {
    if (!actualMap.has(row.valid_time)) actualMap.set(row.valid_time, row.actual_kw ?? null)
    if (!persistenceMap.has(row.valid_time)) persistenceMap.set(row.valid_time, row.persistence_origin_kw ?? null)
    if (!elevationMap.has(row.valid_time)) elevationMap.set(row.valid_time, row.solar_elevation_deg ?? null)
    if (!scenarioMap.has(row.valid_time)) scenarioMap.set(row.valid_time, row.scenario ?? null)
  })

  const predSeriesMap = {}
  const errorSeriesMap = {}
  horizons.forEach(horizon => {
    predSeriesMap[horizon] = timePoints.map(time => {
      const match = findRow(sorted, time, horizon, experiment)
      return match ? (match.prediction_kw ?? null) : null
    })
    errorSeriesMap[horizon] = timePoints.map(time => {
      const match = findRow(sorted, time, horizon, experiment)
      if (!match) return null
      return match.error_kw ?? (match.prediction_kw != null && match.actual_kw != null ? match.prediction_kw - match.actual_kw : null)
    })
  })

  return {
    sorted,
    timePoints,
    actualData: timePoints.map(time => actualMap.get(time) ?? null),
    persistenceData: timePoints.map(time => persistenceMap.get(time) ?? null),
    solarElevations: timePoints.map(time => elevationMap.get(time) ?? 90),
    scenarios: timePoints.map(time => scenarioMap.get(time) ?? ''),
    predSeriesMap,
    errorSeriesMap,
  }
}

function buildDailySeries(rawData, dailySummary, experiment, horizon) {
  const summaryRows = Object.entries(dailySummary || {}).map(([date, summary]) => {
    const horizonSummary = summary?.experiments?.[experiment]?.[String(horizon)] || {}
    return {
      date,
      startDate: date,
      endDate: date,
      label: date,
      windowDays: 1,
      actualKwh: Number(summary.daily_actual_kwh ?? 0),
      predKwh: Number(horizonSummary.daily_pred_kwh ?? NaN),
      errorKwh: Number(horizonSummary.daily_error_kwh ?? NaN),
      scenario: summary.scenario_dominant || 'all',
      count: Number(summary.n_hours ?? 0),
    }
  }).filter(row => row.date && Number.isFinite(row.actualKwh))

  if (summaryRows.length) return summaryRows

  // 后端如果返回小时级数据或某些粒度没有日汇总，前端仍需要能独立聚合，
  // 这样切换 3 日 / 7 日时不会因为接口兼容问题导致空图。
  const grouped = new Map()
  for (const row of rawData || []) {
    if (row.experiment !== experiment || Number(row.horizon_hours) !== Number(horizon)) continue
    const date = toDateKey(row.valid_time || row.valid_date)
    if (!date) continue
    if (!grouped.has(date)) {
      grouped.set(date, { date, actualKwh: 0, predKwh: 0, errorKwh: 0, scenarioVotes: new Map(), count: 0 })
    }
    const day = grouped.get(date)
    day.actualKwh += Number(row.actual_kw ?? 0)
    day.predKwh += Number(row.prediction_kw ?? 0)
    day.errorKwh += Number(row.error_kw ?? ((row.prediction_kw ?? 0) - (row.actual_kw ?? 0)))
    day.count += 1
    const scenario = normalizeScenario(row.scenario)
    day.scenarioVotes.set(scenario, (day.scenarioVotes.get(scenario) || 0) + 1)
  }

  return [...grouped.values()]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map(row => {
      const scenario = [...row.scenarioVotes.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || 'all'
      return {
        ...row,
        startDate: row.date,
        endDate: row.date,
        label: row.date,
        windowDays: 1,
        scenario,
      }
    })
}

function buildWindowedSeries(rows, windowSize) {
  if (windowSize <= 1) return rows
  const result = []

  for (let start = 0; start < rows.length; start += windowSize) {
    const chunk = rows.slice(start, start + windowSize)
    if (!chunk.length) continue
    const actualKwh = chunk.reduce((sum, row) => sum + Number(row.actualKwh || 0), 0)
    const predKwh = chunk.reduce((sum, row) => sum + Number(row.predKwh || 0), 0)
    const scenarioVotes = new Map()
    chunk.forEach(row => {
      const scenario = normalizeScenario(row.scenario)
      scenarioVotes.set(scenario, (scenarioVotes.get(scenario) || 0) + 1)
    })
    const scenario = [...scenarioVotes.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || 'all'
    const startDate = chunk[0].startDate || chunk[0].date
    const endDate = chunk[chunk.length - 1].endDate || chunk[chunk.length - 1].date
    result.push({
      date: `${startDate} ~ ${endDate}`,
      startDate,
      endDate,
      label: `${shortDate(startDate)}~${shortDate(endDate)}`,
      windowDays: chunk.length,
      actualKwh,
      predKwh,
      errorKwh: predKwh - actualKwh,
      scenario,
      count: chunk.reduce((sum, row) => sum + Number(row.count || 0), 0),
    })
  }
  return result
}

function buildHourlyInspectionChart(rawData, options) {
  const {
    horizons = [1, 6, 24],
    experiment = 'stage5',
    scenarioMode = 'all',
  } = options

  const grouped = groupHourlyData(rawData, experiment, horizons)
  if (!grouped.timePoints.length) return emptyOption()

  const scenarioMarkData = buildScenarioMarkAreas(
    grouped.timePoints,
    grouped.solarElevations,
    grouped.scenarios,
    scenarioMode,
  ).map(item => [
    {
      xAxis: item.start[0],
      itemStyle: {
        color: scenarioStyle(item.scenario).area,
        borderColor: scenarioStyle(item.scenario).border,
        borderWidth: 1,
      },
    },
    { xAxis: item.start[1] },
  ])
  const nightMarkData = buildNightMarkAreas(grouped.timePoints, grouped.solarElevations).map(item => [
    { xAxis: item[0], itemStyle: { color: 'rgba(120,126,150,0.18)' } },
    { xAxis: item[1] },
  ])

  const predictionSeries = horizons.map(horizon => ({
    name: horizonLabel(horizon),
    type: 'line',
    data: grouped.predSeriesMap[horizon],
    lineStyle: { color: HORIZON_COLORS[horizon] || '#999', width: 3, type: 'dashed' },
    itemStyle: { color: HORIZON_COLORS[horizon] || '#999' },
    symbol: 'diamond',
    showSymbol: true,
    symbolSize: 5,
    connectNulls: false,
  }))

  const errorSeries = horizons.map(horizon => ({
    name: `${horizonLabel(horizon)} ${SERIES_LABELS.errorPower}`,
    type: 'bar',
    data: grouped.errorSeriesMap[horizon],
    barGap: horizons.length > 1 ? '10%' : '30%',
    barMaxWidth: 20,
    itemStyle: { color: params => (params.value != null && params.value >= 0 ? '#ff5252' : '#64b5f6') },
    markLine: {
      silent: true,
      symbol: 'none',
      animation: false,
      lineStyle: { color: 'rgba(255,255,255,0.34)', type: 'dashed', width: 1 },
      label: { show: false },
      data: [{ yAxis: 0 }],
    },
  }))

  function tooltipFormatter(params) {
    const first = params?.[0]
    if (!first) return ''
    const timeIdx = first.dataIndex
    const time = grouped.timePoints[timeIdx] || ''
    const elevation = grouped.solarElevations[timeIdx]
    const scene = grouped.scenarios[timeIdx]
    const lines = ['<div style="font-size:12px;line-height:1.8;">']
    lines.push(`<div><strong style="color:#00d4ff;">${time}</strong></div>`)
    lines.push(`<div>太阳高度角：${elevation != null ? Number(elevation).toFixed(1) + '°' : '-'}</div>`)
    if (scene) lines.push(`<div>天气场景：${compactScenarioLabel(scene)}</div>`)

    const actual = grouped.actualData[timeIdx]
    if (actual != null) lines.push(`<div><strong>${SERIES_LABELS.actualPower}：</strong>${formatNumber(actual)} kW</div>`)
    const persistence = grouped.persistenceData[timeIdx]
    if (persistence != null) lines.push(`<div><strong>${SERIES_LABELS.persistence}：</strong>${formatNumber(persistence)} kW</div>`)

    horizons.forEach(horizon => {
      const prediction = grouped.predSeriesMap[horizon][timeIdx]
      const error = grouped.errorSeriesMap[horizon][timeIdx]
      const match = findRow(grouped.sorted, time, horizon, experiment)
      const originTime = match?.origin_time || ''
      if (prediction != null) {
        const errorColor = error != null ? (error >= 0 ? '#ff5252' : '#64b5f6') : '#999'
        lines.push('<hr style="border-color:rgba(255,255,255,0.1);margin:4px 0;">')
        lines.push(`<div><strong>${horizonLabel(horizon)}</strong> | 预测起点：${originTime ? originTime.slice(5, 16) : '-'}</div>`)
        lines.push(`<div>预测功率：${formatNumber(prediction)} kW</div>`)
        if (error != null) lines.push(`<div>${SERIES_LABELS.errorPower}：<span style="color:${errorColor};font-weight:600;">${formatNumber(error, 4)}</span> kW</div>`)
      }
    })
    lines.push('</div>')
    return lines.join('')
  }

  const labelInterval = Math.max(0, Math.floor(grouped.timePoints.length / 10))

  return {
    title: {
      text: '实际功率与预测功率对比',
      left: 56,
      top: 6,
      textStyle: { color: 'rgba(255,255,255,0.9)', fontSize: 13, fontWeight: 700 },
    },
    tooltip: { trigger: 'axis', formatter: tooltipFormatter, ...TOOLTIP_STYLE },
    legend: {
      data: [
        { name: SERIES_LABELS.actualPower, icon: 'line' },
        { name: SERIES_LABELS.persistence, icon: 'line' },
        ...horizons.map(horizon => ({ name: horizonLabel(horizon), icon: 'diamond' })),
      ],
      top: 28,
      itemWidth: 16,
      itemHeight: 3,
      textStyle: { color: 'rgba(255,255,255,0.84)', fontSize: 11 },
      pageIconColor: '#00d4ff',
    },
    grid: [
      { id: 'upper', left: 56, right: 24, top: 68, bottom: '34%' },
      { id: 'lower', left: 56, right: 24, top: '69%', bottom: 60 },
    ],
    xAxis: [0, 1].map(gridIndex => ({
      gridIndex,
      type: 'category',
      data: grouped.timePoints,
      axisLabel: {
        formatter: value => String(value).slice(5, 16),
        hideOverlap: true,
        interval: labelInterval,
        ...AXIS_TEXT_STYLE,
      },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.24)' } },
      splitLine: { show: false },
    })),
    yAxis: [
      {
        gridIndex: 0,
        type: 'value',
        name: '功率 (kW)',
        nameTextStyle: AXIS_TEXT_STYLE,
        axisLabel: AXIS_TEXT_STYLE,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)', type: 'dashed' } },
      },
      {
        gridIndex: 1,
        type: 'value',
        name: '误差 (kW)',
        nameTextStyle: AXIS_TEXT_STYLE,
        axisLabel: AXIS_TEXT_STYLE,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)', type: 'dashed' } },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        height: 18,
        bottom: 4,
        borderColor: 'rgba(0,212,255,0.24)',
        backgroundColor: 'rgba(0,0,0,0.2)',
        fillerColor: 'rgba(0,212,255,0.15)',
        handleStyle: { color: '#00d4ff' },
        textStyle: { fontSize: 10, color: 'rgba(255,255,255,0.76)' },
        labelFormatter: value => formatZoomLabel(value, grouped.timePoints),
      },
    ],
    series: [
      {
        name: SERIES_LABELS.actualPower,
        type: 'line',
        data: grouped.actualData,
        lineStyle: { color: '#2ee88b', width: 3.5 },
        itemStyle: { color: '#2ee88b' },
        symbol: 'circle',
        showSymbol: true,
        symbolSize: 5,
        connectNulls: false,
        markArea: { silent: true, data: [...scenarioMarkData, ...nightMarkData] },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: SERIES_LABELS.persistence,
        type: 'line',
        data: grouped.persistenceData,
        lineStyle: { color: '#b8c4d9', width: 1.7, type: 'dashed' },
        itemStyle: { color: '#b8c4d9' },
        symbol: 'triangle',
        showSymbol: true,
        symbolSize: 4,
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      ...predictionSeries.map(series => ({ ...series, xAxisIndex: 0, yAxisIndex: 0 })),
      ...errorSeries.map(series => ({ ...series, xAxisIndex: 1, yAxisIndex: 1 })),
    ],
  }
}

function buildDailyInspectionChart(rawData, options) {
  const {
    dailySummary = {},
    experiment = 'stage5',
    horizons = [1, 6, 24],
    granularity = 'day',
    scenarioMode = 'all',
  } = options
  const mainHorizon = horizons[0] || 6
  const dailyRows = buildDailySeries(rawData, dailySummary, experiment, mainHorizon)
  const windowSize = granularity === '7day' ? 7 : granularity === '3day' ? 3 : 1
  const rows = buildWindowedSeries(dailyRows, windowSize).filter(row => {
    const mode = normalizeScenario(scenarioMode)
    return mode === 'all' || normalizeScenario(row.scenario) === mode
  })

  if (!rows.length) return emptyOption('暂无日粒度数据')

  const categories = rows.map(row => row.label || row.date)
  const actualData = rows.map(row => Number.isFinite(row.actualKwh) ? Number(row.actualKwh.toFixed(3)) : null)
  const predData = rows.map(row => Number.isFinite(row.predKwh) ? Number(row.predKwh.toFixed(3)) : null)
  const errorData = rows.map(row => Number.isFinite(row.errorKwh) ? Number(row.errorKwh.toFixed(3)) : null)
  const scenarioData = rows.map(row => normalizeScenario(row.scenario))
  const title = granularity === 'day'
    ? '实际日发电量与预测日发电量对比'
    : `实际发电量与预测发电量对比（${granularity === '3day' ? '3 日' : '7 日'}窗口）`

  function tooltipFormatter(params) {
    const first = params?.[0]
    if (!first) return ''
    const idx = first.dataIndex
    const row = rows[idx]
    const errorColor = Number(row.errorKwh) >= 0 ? '#ff5252' : '#64b5f6'
    const periodLabel = row.startDate === row.endDate
      ? row.startDate
      : `${row.startDate} ~ ${row.endDate}`
    return [
      '<div style="font-size:12px;line-height:1.8;">',
      `<div><strong style="color:#00d4ff;">${periodLabel}</strong></div>`,
      `<div>覆盖天数：${row.windowDays || 1} 天</div>`,
      `<div>天气场景：${compactScenarioLabel(row.scenario)}</div>`,
      `<div>预测时长：${horizonLabel(mainHorizon)}</div>`,
      `<div><strong>${SERIES_LABELS.actualEnergy}：</strong>${formatNumber(row.actualKwh)} kWh</div>`,
      `<div><strong>${SERIES_LABELS.prediction}：</strong>${formatNumber(row.predKwh)} kWh</div>`,
      `<div>${SERIES_LABELS.errorEnergy}：<span style="color:${errorColor};font-weight:600;">${formatNumber(row.errorKwh)}</span> kWh</div>`,
      '</div>',
    ].join('')
  }

  return {
    title: {
      text: title,
      left: 56,
      top: 6,
      textStyle: { color: 'rgba(255,255,255,0.9)', fontSize: 13, fontWeight: 700 },
    },
    tooltip: { trigger: 'axis', formatter: tooltipFormatter, ...TOOLTIP_STYLE },
    legend: {
      data: [
        { name: SERIES_LABELS.actualEnergy, icon: 'line' },
        { name: `${horizonLabel(mainHorizon)} 预测发电量`, icon: 'line' },
        { name: SERIES_LABELS.errorEnergy, icon: 'rect' },
      ],
      top: 32,
      textStyle: { color: 'rgba(255,255,255,0.84)', fontSize: 11 },
    },
    grid: [
      { id: 'energy', left: 60, right: 28, top: 74, bottom: '35%' },
      { id: 'error', left: 60, right: 28, top: '70%', bottom: 36 },
    ],
    xAxis: [0, 1].map(gridIndex => ({
      gridIndex,
      type: 'category',
      data: categories,
      axisLabel: {
        formatter: value => String(value).replace('~', '\n~'),
        hideOverlap: true,
        ...AXIS_TEXT_STYLE,
      },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.24)' } },
      splitLine: { show: false },
    })),
    yAxis: [
      {
        gridIndex: 0,
        type: 'value',
        name: '发电量 (kWh)',
        nameTextStyle: AXIS_TEXT_STYLE,
        axisLabel: AXIS_TEXT_STYLE,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)', type: 'dashed' } },
      },
      {
        gridIndex: 1,
        type: 'value',
        name: '误差 (kWh)',
        nameTextStyle: AXIS_TEXT_STYLE,
        axisLabel: AXIS_TEXT_STYLE,
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)', type: 'dashed' } },
      },
    ],
    series: [
      {
        name: SERIES_LABELS.actualEnergy,
        type: 'line',
        data: actualData,
        smooth: true,
        symbolSize: 8,
        itemStyle: { color: '#2ee88b' },
        lineStyle: { color: '#2ee88b', width: 3.2 },
        areaStyle: { color: 'rgba(46,232,139,0.08)' },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: `${horizonLabel(mainHorizon)} 预测发电量`,
        type: 'line',
        data: predData,
        smooth: true,
        symbol: 'diamond',
        symbolSize: 8,
        itemStyle: { color: '#38bdf8' },
        lineStyle: { color: '#38bdf8', width: 3, type: scenarioStyle(scenarioMode).lineType },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        name: SERIES_LABELS.errorEnergy,
        type: 'bar',
        data: errorData,
        barMaxWidth: 34,
        itemStyle: {
          color: params => {
            if (params.value == null) return 'rgba(255,255,255,0.18)'
            return params.value >= 0 ? '#ff5252' : '#64b5f6'
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          animation: false,
          lineStyle: { color: 'rgba(255,255,255,0.34)', type: 'dashed', width: 1 },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
      ...Object.keys(SCENARIO_STYLES)
        .filter(key => key !== 'all')
        .map(key => ({
          name: SCENARIO_STYLES[key].label,
          type: 'line',
          data: scenarioData.map((scenario, idx) => (scenario === key ? predData[idx] : null)),
          connectNulls: false,
          showSymbol: false,
          lineStyle: {
            color: SCENARIO_STYLES[key].color,
            width: 2,
            type: SCENARIO_STYLES[key].lineType,
          },
          emphasis: { disabled: true },
          tooltip: { show: false },
          xAxisIndex: 0,
          yAxisIndex: 0,
        })),
    ],
  }
}

export function buildInspectionChart(rawData, options = {}) {
  const granularity = options.granularity || 'hour'
  if (!rawData || !rawData.length) return emptyOption()
  if (granularity === 'hour') return buildHourlyInspectionChart(rawData, options)
  return buildDailyInspectionChart(rawData, { ...options, granularity })
}
