/**
 * ECharts admin theme registration.
 * Import this file once in main.js to register the 'dark-tech' theme globally.
 */
import * as echarts from 'echarts/core'

const darkTechTheme = {
  color: ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4'],
  backgroundColor: 'transparent',
  textStyle: { color: '#303133', fontFamily: 'Inter, sans-serif' },
  title: {
    textStyle: { color: '#303133', fontSize: 16, fontWeight: 600 },
    subtextStyle: { color: '#909399', fontSize: 12 },
  },
  legend: {
    textStyle: { color: '#606266' },
  },
  tooltip: {
    backgroundColor: 'rgba(255,255,255,0.98)',
    borderColor: '#dcdfe6',
    borderWidth: 1,
    textStyle: { color: '#303133', fontSize: 13 },
    extraCssText: 'border-radius: 4px; box-shadow: 0 4px 16px rgba(31,45,61,0.12);',
  },
  grid: {
    borderColor: '#ebeef5',
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: '#dcdfe6' } },
    axisTick: { lineStyle: { color: '#ebeef5' } },
    axisLabel: { color: '#606266' },
    splitLine: { lineStyle: { color: '#ebeef5' } },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: '#dcdfe6' } },
    axisTick: { lineStyle: { color: '#ebeef5' } },
    axisLabel: { color: '#606266' },
    splitLine: { lineStyle: { color: '#ebeef5', type: 'solid' } },
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
    axisLine: { lineStyle: { color: '#dcdfe6' } },
    splitLine: { lineStyle: { color: '#ebeef5' } },
    splitArea: { areaStyle: { color: ['rgba(64,158,255,0.03)', 'rgba(64,158,255,0.06)'] } },
  },
  gauge: {
    axisLine: { lineStyle: { color: [[0.3, '#ff5252'], [0.7, '#ffa726'], [1, '#00f5a0']] } },
  },
}

echarts.registerTheme('dark-tech', darkTechTheme)

export default darkTechTheme
