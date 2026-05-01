import { chartColors } from './chartTheme'

export function buildPredictionChartOption(predictions) {
  if (!predictions.length) return {}

  const timestamps = predictions.map(row => row.timestamp || row.delivery_timestamp || '')
  const actual = predictions.map(row => row.actual_kw ?? null)
  const predicted = predictions.map(row => row.prediction_kw ?? null)

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['Actual', 'Predicted'], top: 8, textStyle: { color: 'rgba(255,255,255,0.78)' } },
    grid: { left: 58, right: 26, top: 48, bottom: 64 },
    xAxis: {
      type: 'category',
      data: timestamps,
      axisLabel: {
        formatter: value => value.slice(5, 11),
        hideOverlap: true,
        interval: Math.max(0, Math.floor(timestamps.length / 8)),
        fontSize: 11,
      },
    },
    yAxis: { type: 'value', name: 'Power (kW)' },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
    series: [
      {
        name: 'Actual',
        type: 'line',
        data: actual,
        lineStyle: { color: chartColors.green, width: 1.5 },
        itemStyle: { color: chartColors.green },
        showSymbol: false,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(0,245,160,0.15)' },
              { offset: 1, color: 'rgba(0,245,160,0)' },
            ],
          },
        },
      },
      {
        name: 'Predicted',
        type: 'line',
        data: predicted,
        lineStyle: { color: chartColors.cyan, width: 1.5, type: 'dashed' },
        itemStyle: { color: chartColors.cyan },
        showSymbol: false,
      },
    ],
  }
}
