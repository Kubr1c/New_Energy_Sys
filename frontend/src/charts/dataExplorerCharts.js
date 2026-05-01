export function buildFeatureImportanceOption(features) {
  if (!features.length) return {}

  const top20 = features
    .map(feature => ({ ...feature, score: Number(feature.importance ?? feature.gain ?? 0) }))
    .filter(feature => feature.score > 0)
    .slice(0, 20)
    .reverse()
  if (!top20.length) return {}

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 210, right: 36, top: 12, bottom: 34 },
    xAxis: { type: 'value', name: 'Importance' },
    yAxis: {
      type: 'category',
      data: top20.map(feature => feature.feature || feature.name || ''),
      axisLabel: { fontSize: 11, color: 'rgba(255,255,255,0.78)' },
    },
    series: [
      {
        type: 'bar',
        data: top20.map((feature, index) => ({
          value: Number(feature.score.toFixed(4)),
          itemStyle: { color: `hsl(${190 + index * 4}, 80%, 55%)` },
        })),
        barMaxWidth: 18,
      },
    ],
  }
}
