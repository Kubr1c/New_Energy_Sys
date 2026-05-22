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
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#dcdfe6',
      borderWidth: 1,
      textStyle: { color: '#303133' },
      extraCssText: 'box-shadow: 0 10px 28px rgba(15, 23, 42, 0.14);',
      formatter(params) {
        const item = params?.[0]
        const row = top20[item?.dataIndex]
        if (!row) return ''
        return [
          `<strong>${featureFieldLabel(row.rawName)}</strong>`,
          `模型内部重要性分数：${Number(row.score).toFixed(4)}`,
          `原始字段：${row.rawName || '-'}`,
        ].join('<br/>')
      },
    },
    grid: { left: 230, right: 92, top: 18, bottom: 40 },
    xAxis: {
      type: 'value',
      name: '模型内部重要性',
      nameTextStyle: { color: '#606266' },
      axisLabel: { color: '#606266' },
      splitLine: { lineStyle: { color: '#ebeef5' } },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    yAxis: {
      type: 'category',
      data: top20.map(feature => featureFieldLabel(feature.rawName)),
      axisLabel: {
        width: 210,
        overflow: 'truncate',
        fontSize: 11,
        color: '#606266',
      },
      axisLine: { lineStyle: { color: '#dcdfe6' } },
    },
    series: [
      {
        type: 'bar',
        data: top20.map((feature, index) => ({
          value: Number(feature.score.toFixed(4)),
          itemStyle: { color: `hsl(${205 + index * 3}, 72%, 48%)` },
        })),
        label: {
          show: true,
          position: 'right',
          formatter: params => Number(params.value).toFixed(2),
          color: '#303133',
          fontSize: 11,
        },
        barMaxWidth: 18,
      },
    ],
  }
}
