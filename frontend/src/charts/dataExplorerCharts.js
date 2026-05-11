import { featureFieldLabel } from '../utils/displayLabels'

export function featureImportanceScore(feature) {
  return Number(feature.importance ?? feature.importance_gain ?? feature.gain ?? feature.importance_split ?? 0)
}

export function buildFeatureImportanceOption(features) {
  if (!features.length) return {}

  const top20 = features
    .map(feature => ({
      ...feature,
      score: featureImportanceScore(feature),
      rawName: feature.feature || feature.name || '',
    }))
    .filter(feature => feature.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 20)
    .reverse()
  if (!top20.length) return {}

  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const item = params?.[0]
        const row = top20[item?.dataIndex]
        if (!row) return ''
        return [
          `<strong>${featureFieldLabel(row.rawName)}</strong>`,
          `相对重要性：${Number(row.score).toFixed(4)}`,
          `原始字段：${row.rawName || '-'}`,
        ].join('<br/>')
      },
    },
    grid: { left: 230, right: 48, top: 18, bottom: 40 },
    xAxis: {
      type: 'value',
      name: '相对重要性',
      nameTextStyle: { color: 'rgba(255,255,255,0.74)' },
      axisLabel: { color: 'rgba(255,255,255,0.74)' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.10)' } },
    },
    yAxis: {
      type: 'category',
      data: top20.map(feature => featureFieldLabel(feature.rawName)),
      axisLabel: {
        width: 210,
        overflow: 'truncate',
        fontSize: 11,
        color: 'rgba(255,255,255,0.82)',
      },
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
