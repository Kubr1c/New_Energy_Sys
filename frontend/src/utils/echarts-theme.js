/**
 * ECharts dark-tech theme registration.
 * Import this file once in main.js to register the 'dark-tech' theme globally.
 */
import * as echarts from 'echarts/core'

const darkTechTheme = {
  color: ['#00d4ff', '#00f5a0', '#ffa726', '#ff5252', '#b388ff', '#64b5f6', '#ffee58', '#4dd0e1'],
  backgroundColor: 'transparent',
  textStyle: { color: 'rgba(255,255,255,0.85)', fontFamily: 'Inter, sans-serif' },
  title: {
    textStyle: { color: 'rgba(255,255,255,0.92)', fontSize: 16, fontWeight: 600 },
    subtextStyle: { color: 'rgba(255,255,255,0.45)', fontSize: 12 },
  },
  legend: {
    textStyle: { color: 'rgba(255,255,255,0.65)' },
  },
  tooltip: {
    backgroundColor: 'rgba(10, 14, 39, 0.92)',
    borderColor: 'rgba(0, 212, 255, 0.25)',
    borderWidth: 1,
    textStyle: { color: 'rgba(255,255,255,0.9)', fontSize: 13 },
    extraCssText: 'backdrop-filter: blur(12px); border-radius: 8px; box-shadow: 0 4px 24px rgba(0,0,0,0.4);',
  },
  grid: {
    borderColor: 'rgba(255,255,255,0.06)',
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
    axisTick: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
    axisLabel: { color: 'rgba(255,255,255,0.55)' },
    splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
    axisTick: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
    axisLabel: { color: 'rgba(255,255,255,0.55)' },
    splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)', type: 'dashed' } },
  },
  line: {
    smooth: true,
    symbolSize: 4,
    lineStyle: { width: 2.5 },
  },
  bar: {
    barMaxWidth: 28,
    itemStyle: { borderRadius: [4, 4, 0, 0] },
  },
  radar: {
    axisLine: { lineStyle: { color: 'rgba(255,255,255,0.12)' } },
    splitLine: { lineStyle: { color: 'rgba(255,255,255,0.08)' } },
    splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0.04)'] } },
  },
  gauge: {
    axisLine: { lineStyle: { color: [[0.3, '#ff5252'], [0.7, '#ffa726'], [1, '#00f5a0']] } },
  },
}

echarts.registerTheme('dark-tech', darkTechTheme)

export default darkTechTheme
