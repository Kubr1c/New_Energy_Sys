/**
 * Inspection Dashboard — ECharts Two-Grid Chart Builder
 *
 * Layout:
 *   Upper grid (~70%): Actual (solid black) vs Predictions (dashed, per horizon)
 *                       + Persistence (gray dashed) + Night/Scenario markArea
 *   Lower grid (~30%): Error Residual bars (red=over, blue=under)
 *
 * Both grids share the xAxis (time). A dataZoom slider sits at the bottom.
 */

// ---------------------------------------------------------------------------
// Helpers: markArea data generation
// ---------------------------------------------------------------------------

/**
 * Build night markArea segments from solar_elevation_deg array.
 * Marks periods where solar_elevation <= 5 degrees.
 * Returns array of [startCategory, endCategory] pairs.
 */
function buildNightMarkAreas(timePoints, solarElevations) {
  const marks = []
  let startIdx = -1

  for (let i = 0; i < solarElevations.length; i++) {
    const isNight = solarElevations[i] <= 5
    if (isNight && startIdx === -1) {
      startIdx = i
    } else if (!isNight && startIdx !== -1) {
      marks.push([timePoints[startIdx], timePoints[i - 1]])
      startIdx = -1
    }
  }
  // Close trailing night segment
  if (startIdx !== -1) {
    marks.push([timePoints[startIdx], timePoints[timePoints.length - 1]])
  }
  return marks
}

/**
 * Build daytime scenario markArea segments.
 * Groups consecutive same-scenario daytime points into colored bands.
 * Filters by scenarioMode when not 'all'.
 *
 * Returns array of { start: [startVal, endVal], scenario } objects.
 */
function buildScenarioMarkAreas(timePoints, solarElevations, scenarios, scenarioMode) {
  const marks = []
  let startIdx = -1
  let currentScenario = null

  for (let i = 0; i < timePoints.length; i++) {
    const isDaytime = solarElevations[i] > 5
    const sc = isDaytime ? scenarios[i] : null

    if (isDaytime && sc !== null) {
      if (startIdx === -1) {
        // Entering daytime
        startIdx = i
        currentScenario = sc
      } else if (sc !== currentScenario) {
        // Scenario changed — close old segment, open new
        marks.push({ start: [timePoints[startIdx], timePoints[i - 1]], scenario: currentScenario })
        startIdx = i
        currentScenario = sc
      }
    } else if (!isDaytime && startIdx !== -1) {
      // Exiting daytime — close segment
      marks.push({ start: [timePoints[startIdx], timePoints[i - 1]], scenario: currentScenario })
      startIdx = -1
      currentScenario = null
    }
  }
  // Handle last segment if still in daytime
  if (startIdx !== -1) {
    marks.push({ start: [timePoints[startIdx], timePoints[timePoints.length - 1]], scenario: currentScenario })
  }

  // Filter by scenario display mode
  if (scenarioMode && scenarioMode !== 'all') {
    return marks.filter(m => m.scenario === scenarioMode)
  }
  return marks
}

// ---------------------------------------------------------------------------
// Horizon configuration
// ---------------------------------------------------------------------------

const HORIZON_COLORS = {
  1: '#2ecc71',  // green — t+1h
  6: '#3498db',  // blue  — t+6h
  24: '#e74c3c', // red   — t+24h
}
const HORIZON_LABELS = {
  1: 't+1h',
  6: 't+6h',
  24: 't+24h',
}
const SCENARIO_BG_COLORS = {
  clear: 'rgba(255,255,200,0.12)',
  mixed: 'rgba(255,200,100,0.12)',
  overcast: 'rgba(180,180,180,0.12)',
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Build a complete ECharts option object for the inspection two-grid chart.
 *
 * @param {Array} rawData - API response data array
 * @param {Object} options
 * @param {number[]} options.horizons   - Selected horizons (default [1,6,24])
 * @param {string}   options.experiment - Selected experiment key (default 'stage5')
 * @param {string}   options.scenarioMode - Scenario display filter (default 'all')
 * @returns {Object} ECharts option
 */
export function buildInspectionChart(rawData, options = {}) {
  const {
    horizons = [1, 6, 24],
    experiment = 'stage5',
    scenarioMode = 'all',
  } = options

  // Guard: no data → empty option
  if (!rawData || !rawData.length) {
    return {
      title: { text: '暂无数据', left: 'center', top: 'center', textStyle: { color: 'rgba(255,255,255,0.4)', fontSize: 14 } },
      grid: [],
      xAxis: [],
      yAxis: [],
      series: [],
    }
  }

  // -----------------------------------------------------------------------
  // 1. Sort & build deduplicated time axis
  // -----------------------------------------------------------------------
  const sorted = [...rawData].sort(
    (a, b) => new Date(a.valid_time) - new Date(b.valid_time)
  )

  const timeSet = new Set()
  sorted.forEach(d => timeSet.add(d.valid_time))
  const timePoints = [...timeSet].sort(
    (a, b) => new Date(a) - new Date(b)
  )

  // -----------------------------------------------------------------------
  // 2. Deduplicate per-valid_time fields (actual, persistence, elevation, scenario)
  // -----------------------------------------------------------------------
  const actualMap = new Map()
  const persistenceMap = new Map()
  const elevationMap = new Map()
  const scenarioMap = new Map()

  sorted.forEach(d => {
    if (!actualMap.has(d.valid_time)) actualMap.set(d.valid_time, d.actual_kw ?? null)
    if (!persistenceMap.has(d.valid_time)) persistenceMap.set(d.valid_time, d.persistence_origin_kw ?? null)
    if (!elevationMap.has(d.valid_time)) elevationMap.set(d.valid_time, d.solar_elevation_deg ?? null)
    if (!scenarioMap.has(d.valid_time)) scenarioMap.set(d.valid_time, d.scenario ?? null)
  })

  const actualData = timePoints.map(t => actualMap.get(t) ?? null)
  const persistenceData = timePoints.map(t => persistenceMap.get(t) ?? null)
  const solarElevations = timePoints.map(t => elevationMap.get(t) ?? 90)
  const scenarios = timePoints.map(t => scenarioMap.get(t) ?? '')

  // -----------------------------------------------------------------------
  // 3. Build per-horizon prediction and error arrays
  // -----------------------------------------------------------------------
  const predSeriesMap = {}
  const errorSeriesMap = {}

  horizons.forEach(h => {
    predSeriesMap[h] = timePoints.map(t => {
      const match = sorted.find(
        d => d.valid_time === t && d.horizon_hours === h && d.experiment === experiment
      )
      return match ? (match.prediction_kw ?? null) : null
    })

    errorSeriesMap[h] = timePoints.map(t => {
      const match = sorted.find(
        d => d.valid_time === t && d.horizon_hours === h && d.experiment === experiment
      )
      if (!match) return null
      const error = match.error_kw ?? (match.prediction_kw != null && match.actual_kw != null
        ? match.prediction_kw - match.actual_kw
        : null)
      return error
    })
  })

  // -----------------------------------------------------------------------
  // 4. markArea data — night and scenario
  // -----------------------------------------------------------------------
  const nightMarks = buildNightMarkAreas(timePoints, solarElevations)
  const scenarioMarks = buildScenarioMarkAreas(timePoints, solarElevations, scenarios, scenarioMode)

  const scenarioMarkData = scenarioMarks.map(m => [
    {
      xAxis: m.start[0],
      itemStyle: {
        color: SCENARIO_BG_COLORS[m.scenario] || 'rgba(200,200,200,0.1)',
      },
    },
    { xAxis: m.start[1] },
  ])

  const nightMarkData = nightMarks.map(m => [
    {
      xAxis: m[0],
      itemStyle: { color: 'rgba(128,128,128,0.25)' },
    },
    { xAxis: m[1] },
  ])

  // -----------------------------------------------------------------------
  // 5. Series definitions
  // -----------------------------------------------------------------------

  // -- Upper grid: actual + persistence + predictions
  const predictionSeries = horizons.map(h => ({
    name: HORIZON_LABELS[h] || `t+${h}h`,
    type: 'line',
    data: predSeriesMap[h],
    lineStyle: { color: HORIZON_COLORS[h] || '#999', width: 2, type: 'dashed' },
    itemStyle: { color: HORIZON_COLORS[h] || '#999' },
    showSymbol: false,
    smooth: true,
    connectNulls: false,
    // Mark lines for individual series legend — not needed here
  }))

  // Build combined markArea for the actual series (scenario then night, so night wins)
  const combinedMarkData = [...scenarioMarkData, ...nightMarkData]

  const upperSeries = [
    {
      name: 'Actual',
      type: 'line',
      data: actualData,
      lineStyle: { color: '#ffffff', width: 3 },
      itemStyle: { color: '#ffffff' },
      showSymbol: false,
      smooth: true,
      // Background shading (night + scenario)
      ...(combinedMarkData.length > 0
        ? {
            markArea: {
              silent: true,
              data: combinedMarkData,
            },
          }
        : {}),
    },
    {
      name: 'Persistence',
      type: 'line',
      data: persistenceData,
      lineStyle: { color: '#8a92a6', width: 1.5, type: 'dashed' },
      itemStyle: { color: '#8a92a6' },
      showSymbol: false,
      smooth: true,
    },
    ...predictionSeries,
  ]

  // -- Lower grid: error bars per horizon
  const errorSeries = horizons.map(h => ({
    name: `${HORIZON_LABELS[h] || `t+${h}h`} Error`,
    type: 'bar',
    data: errorSeriesMap[h],
    barGap: horizons.length > 1 ? '10%' : '30%',
    barMaxWidth: 20,
    itemStyle: {
      color: (params) => {
        const val = params.value
        return val != null && val >= 0 ? '#ff5252' : '#64b5f6'
      },
    },
    emphasis: { itemStyle: { opacity: 0.8 } },
    markLine: {
      silent: true,
      symbol: 'none',
      animation: false,
      lineStyle: { color: 'rgba(255,255,255,0.25)', type: 'dashed', width: 1 },
      label: { show: false },
      data: [{ yAxis: 0 }],
    },
  }))

  // -----------------------------------------------------------------------
  // 6. Tooltip formatter
  // -----------------------------------------------------------------------
  function tooltipFormatter(params) {
    const first = params?.[0]
    if (!first) return ''
    const timeIdx = first.dataIndex
    const vt = timePoints[timeIdx] || ''
    const se = solarElevations[timeIdx]
    const sc = scenarios[timeIdx]

    const lines = [`<div style="font-size:12px;line-height:1.8;">`]
    lines.push(`<div style="margin-bottom:4px;"><strong style="color:#00d4ff;">${vt}</strong></div>`)
    lines.push(`<div>太阳高度角: ${se != null ? se.toFixed(1) + '°' : '-'}</div>`)
    if (sc) lines.push(`<div>天气场景: ${sc}</div>`)
    lines.push(`<hr style="border-color:rgba(255,255,255,0.1);margin:4px 0;">`)

    // Actual value
    const av = actualData[timeIdx]
    if (av != null) lines.push(`<div><span style="display:inline-block;width:12px;height:3px;background:#fff;border-radius:2px;vertical-align:middle;margin-right:6px;"></span><strong>Actual:</strong> ${av.toFixed(3)} kW</div>`)

    // Persistence
    const pv = persistenceData[timeIdx]
    if (pv != null) lines.push(`<div><span style="display:inline-block;width:12px;height:3px;background:#8a92a6;border-radius:2px;vertical-align:middle;margin-right:6px;"></span><strong>Persistence:</strong> ${pv.toFixed(3)} kW</div>`)

    // Per-horizon
    horizons.forEach(h => {
      const predVal = predSeriesMap[h][timeIdx]
      const errVal = errorSeriesMap[h][timeIdx]
      // Find origin_time from raw data
      const match = sorted.find(
        d => d.valid_time === vt && d.horizon_hours === h && d.experiment === experiment
      )
      const originTime = match?.origin_time || ''

      if (predVal != null) {
        const color = HORIZON_COLORS[h] || '#999'
        const errColor = errVal != null ? (errVal >= 0 ? '#ff5252' : '#64b5f6') : '#999'
        lines.push(`<hr style="border-color:rgba(255,255,255,0.1);margin:4px 0;">`)
        lines.push(`<div><span style="display:inline-block;width:12px;height:3px;background:${color};border-radius:2px;vertical-align:middle;margin-right:6px;"></span><strong>${HORIZON_LABELS[h] || `t+${h}h`}</strong>  |  预报起始: ${originTime ? originTime.slice(5, 16) : '-'}</div>
        <div style="padding-left:18px;">Prediction: ${predVal.toFixed(3)} kW</div>
        ${errVal != null ? `<div style="padding-left:18px;">Error: <span style="color:${errColor};font-weight:600;">${errVal.toFixed(4)}</span> kW</div>` : ''}`)
      }
    })

    lines.push(`</div>`)
    return lines.join('')
  }

  // -----------------------------------------------------------------------
  // 7. Assemble full ECharts option
  // -----------------------------------------------------------------------

  // Compute label interval to avoid overcrowding
  const labelInterval = Math.max(0, Math.floor(timePoints.length / 10))

  return {
    // ---- Tooltip ----
    tooltip: {
      trigger: 'axis',
      formatter: tooltipFormatter,
      backgroundColor: 'rgba(10,14,39,0.94)',
      borderColor: 'rgba(0,212,255,0.3)',
      borderWidth: 1,
      textStyle: { color: 'rgba(255,255,255,0.9)', fontSize: 12 },
      confine: true,
    },

    // ---- Legend ----
    legend: {
      data: [
        { name: 'Actual', icon: 'line' },
        { name: 'Persistence', icon: 'line' },
        ...horizons.map(h => ({
          name: HORIZON_LABELS[h] || `t+${h}h`,
          icon: 'diamond',
        })),
      ],
      top: 6,
      itemWidth: 16,
      itemHeight: 3,
      textStyle: { color: 'rgba(255,255,255,0.75)', fontSize: 11 },
      pageIconColor: '#00d4ff',
    },

    // ---- Grid layout ----
    grid: [
      {
        id: 'upper',
        left: 56,
        right: 24,
        top: 44,
        bottom: '34%', // leave room for lower grid
      },
      {
        id: 'lower',
        left: 56,
        right: 24,
        top: '69%',
        bottom: 60, // room for dataZoom slider
      },
    ],

    // ---- xAxes (shared category) ----
    xAxis: [
      {
        gridIndex: 0,
        type: 'category',
        data: timePoints,
        axisLabel: {
          formatter: (v) => v.slice(5, 16),
          hideOverlap: true,
          interval: labelInterval,
          fontSize: 10,
          color: 'rgba(255,255,255,0.5)',
        },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
        splitLine: { show: false },
      },
      {
        gridIndex: 1,
        type: 'category',
        data: timePoints,
        axisLabel: {
          formatter: (v) => v.slice(5, 16),
          hideOverlap: true,
          interval: labelInterval,
          fontSize: 10,
          color: 'rgba(255,255,255,0.5)',
        },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
        splitLine: { show: false },
      },
    ],

    // ---- yAxes ----
    yAxis: [
      {
        gridIndex: 0,
        type: 'value',
        name: 'Power (kW)',
        nameTextStyle: { fontSize: 10, color: 'rgba(255,255,255,0.5)' },
        axisLabel: { fontSize: 10, color: 'rgba(255,255,255,0.5)' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)', type: 'dashed' } },
      },
      {
        gridIndex: 1,
        type: 'value',
        name: 'Error (kW)',
        nameTextStyle: { fontSize: 10, color: 'rgba(255,255,255,0.5)' },
        axisLabel: { fontSize: 10, color: 'rgba(255,255,255,0.5)' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)', type: 'dashed' } },
      },
    ],

    // ---- DataZoom ----
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: 0,
        end: 100,
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        height: 18,
        bottom: 4,
        borderColor: 'rgba(0,212,255,0.2)',
        backgroundColor: 'rgba(0,0,0,0.2)',
        fillerColor: 'rgba(0,212,255,0.15)',
        handleStyle: { color: '#00d4ff' },
        textStyle: { fontSize: 10, color: 'rgba(255,255,255,0.5)' },
        labelFormatter: (v) => (v ? v.slice(5, 16) : ''),
      },
    ],

    // ---- Series ----
    series: [
      // Upper grid: actual + persistence + predictions
      ...upperSeries.map(s => ({ ...s, xAxisIndex: 0, yAxisIndex: 0 })),
      // Lower grid: error bars per horizon
      ...errorSeries.map(s => ({ ...s, xAxisIndex: 1, yAxisIndex: 1 })),
    ],
  }
}
