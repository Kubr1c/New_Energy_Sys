<template>
  <div class="overview">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载系统总览"
      message="正在读取站点配置、主模型指标和预测曲线。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="系统总览加载失败"
      :message="error.message"
      retryable
      @retry="loadData"
    />
    <PageState
      v-else-if="!hasOverviewData"
      type="empty"
      title="暂无系统总览数据"
      message="当前后端没有返回站点配置、模型指标或预测曲线。"
      retryable
      @retry="loadData"
    />
    <template v-else>
    <!-- KPI Cards -->
    <div class="kpi-row">
      <div v-for="(kpi, i) in kpiCards" :key="kpi.label" class="kpi-card glass-card animate-fade-in-up" :class="'animate-delay-' + (i + 1)">
        <div class="kpi-icon" :style="{ background: kpi.gradient }">
          <el-icon :size="22"><component :is="kpi.icon" /></el-icon>
        </div>
        <div class="kpi-body">
          <div class="kpi-value display-number">{{ kpi.value }}</div>
          <div class="kpi-label">{{ kpi.label }}</div>
        </div>
      </div>
    </div>

    <!-- Main Row: Prediction Chart + Site Info -->
    <div class="main-row">
      <div class="chart-section glass-card animate-fade-in-up animate-delay-2">
        <div class="section-header">
          <h3>PV 功率预测 vs 实际 — Prediction vs Actual</h3>
          <div class="header-actions">
            <el-select v-model="predDays" size="small" style="width:100px" @change="loadPredictions">
              <el-option label="7 天" :value="168" />
              <el-option label="14 天" :value="336" />
              <el-option label="30 天" :value="720" />
            </el-select>
          </div>
        </div>
        <v-chart class="main-chart" :option="predChartOption" theme="dark-tech" autoresize />
      </div>

      <div class="info-section">
        <div class="glass-card site-card animate-fade-in-up animate-delay-3">
          <h3>站点信息 Site Info</h3>
          <div class="site-detail" v-if="siteConfig.site">
            <div class="detail-row">
              <span class="detail-label">Site</span>
              <span class="detail-value">PVDAQ System 10</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Location</span>
              <span class="detail-value">{{ siteConfig.site.latitude }}°N, {{ Math.abs(siteConfig.site.longitude) }}°W</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Capacity</span>
              <span class="detail-value display-number">{{ siteConfig.site.capacity_kw }} kW</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Storage</span>
              <span class="detail-value display-number">{{ siteConfig.storage?.capacity_kwh }} kWh</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Period</span>
              <span class="detail-value">2020-01 ~ 2022-12</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Main Model</span>
              <span class="detail-value" style="color: var(--accent-cyan)">LightGBM (tuned)</span>
            </div>
          </div>
        </div>

        <div class="glass-card pipeline-card animate-fade-in-up animate-delay-4">
          <h3>技术路线 Pipeline</h3>
          <div class="pipeline-stages">
            <div v-for="stage in pipelineStages" :key="stage.id" class="stage-item" :class="{ completed: stage.done }">
              <div class="stage-dot"></div>
              <div class="stage-info">
                <span class="stage-id">{{ stage.id }}</span>
                <span class="stage-name">{{ stage.name }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent, DataZoomComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import api from '../utils/api'
import PageState from '../components/PageState.vue'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, LineChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, DataZoomComponent])

const siteConfig = ref({})
const mainMetrics = ref([])
const predictions = ref([])
const predDays = ref(168)
const loading = ref(false)
const error = ref(null)

const hasOverviewData = computed(() => {
  return Boolean(siteConfig.value?.site) || mainMetrics.value.length > 0 || predictions.value.length > 0
})

const kpiCards = computed(() => {
  const m = mainMetrics.value.find(r => r.split === 'test') || {}
  return [
    { label: 'PV 容量 Capacity', value: siteConfig.value?.site?.capacity_kw ? siteConfig.value.site.capacity_kw + ' kW' : '—', icon: 'Sunny', gradient: 'var(--gradient-orange)' },
    { label: '主模型 nRMSE', value: m.nrmse_capacity ? Number(m.nrmse_capacity).toFixed(4) : '—', icon: 'TrendCharts', gradient: 'var(--gradient-cyan)' },
    { label: '日间 nRMSE', value: m.daytime_nrmse_capacity ? Number(m.daytime_nrmse_capacity).toFixed(4) : '—', icon: 'Sunrise', gradient: 'var(--gradient-green)' },
    { label: '储能容量 Storage', value: siteConfig.value?.storage?.capacity_kwh ? siteConfig.value.storage.capacity_kwh + ' kWh' : '—', icon: 'Coin', gradient: 'var(--gradient-purple)' },
  ]
})

const predChartOption = computed(() => {
  if (!predictions.value.length) return {}
  const data = predictions.value
  const timestamps = data.map(r => r.timestamp || r.delivery_timestamp || '')
  const actual = data.map(r => r.actual_kw ?? null)
  const predicted = data.map(r => r.prediction_kw ?? null)

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['Actual', 'Predicted'], top: 10 },
    grid: { left: 50, right: 30, top: 50, bottom: 60 },
    xAxis: { type: 'category', data: timestamps, axisLabel: { formatter: v => v.slice(5, 16), rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value', name: 'Power (kW)' },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 5 }],
    series: [
      { name: 'Actual', type: 'line', data: actual, lineStyle: { color: '#00f5a0', width: 1.5 }, itemStyle: { color: '#00f5a0' }, showSymbol: false, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(0,245,160,0.15)' }, { offset: 1, color: 'rgba(0,245,160,0)' }] } } },
      { name: 'Predicted', type: 'line', data: predicted, lineStyle: { color: '#00d4ff', width: 1.5, type: 'dashed' }, itemStyle: { color: '#00d4ff' }, showSymbol: false },
    ],
  }
})

const pipelineStages = [
  { id: 'S1-S3', name: '数据采集与特征工程', done: true },
  { id: 'S4-S5', name: 'LightGBM 基线与调参', done: true },
  { id: 'S6', name: 'TCN 序列预测', done: true },
  { id: 'S7', name: '天气预报验证', done: true },
  { id: 'S8-S9', name: '多模型对比与推理固化', done: true },
  { id: 'S10-S12', name: '储能调度仿真', done: true },
  { id: 'S13', name: '策略治理与评分', done: true },
  { id: 'S14', name: '深度学习补强', done: true },
  { id: 'S15', name: '储能配置敏感性', done: true },
]

async function loadData() {
  loading.value = true
  error.value = null
  try {
    const [configRes, metricsRes] = await Promise.all([
      api.get('/api/config'),
      api.get('/api/models/main'),
    ])
    siteConfig.value = configRes.data
    mainMetrics.value = metricsRes.data
    await loadPredictions()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

async function loadPredictions() {
  try {
    const res = await api.get('/api/predictions/main', { params: { limit: predDays.value } })
    predictions.value = res.data
  } catch (e) { error.value = e.normalized || normalizeApiError(e) }
}

onMounted(loadData)
</script>

<style scoped>
.overview { display: flex; flex-direction: column; gap: var(--space-xl); }

.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-lg); }
.kpi-card { display: flex; align-items: center; gap: var(--space-md); padding: 20px 24px; }
.kpi-icon {
  width: 44px; height: 44px; border-radius: var(--radius-md);
  display: flex; align-items: center; justify-content: center;
  color: #fff; flex-shrink: 0;
}
.kpi-value { font-size: 22px; color: var(--text-primary); line-height: 1.2; }
.kpi-label { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

.main-row { display: grid; grid-template-columns: 1fr 340px; gap: var(--space-lg); }
.chart-section { padding: var(--space-lg); }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-md); }
.section-header h3 { font-size: 15px; font-weight: 600; }
.main-chart { height: 400px; width: 100%; }

.info-section { display: flex; flex-direction: column; gap: var(--space-lg); }
.site-card, .pipeline-card { padding: var(--space-lg); }
.site-card h3, .pipeline-card h3 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-md); color: var(--accent-cyan); }

.detail-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border-glass); }
.detail-label { font-size: 12px; color: var(--text-secondary); }
.detail-value { font-size: 13px; color: var(--text-primary); }

.pipeline-stages { display: flex; flex-direction: column; gap: 8px; }
.stage-item { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
.stage-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--text-tertiary);
  flex-shrink: 0;
  transition: all var(--duration-fast) var(--ease-default);
}
.stage-item.completed .stage-dot { background: var(--accent-green); box-shadow: var(--shadow-glow-green); }
.stage-id { font-size: 11px; color: var(--accent-cyan); font-weight: 600; min-width: 50px; font-family: var(--font-mono); }
.stage-name { font-size: 12px; color: var(--text-secondary); }
.stage-item.completed .stage-name { color: var(--text-primary); }

@media (max-width: 1199px) {
  .kpi-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .main-row { grid-template-columns: 1fr; }
  .chart-section, .site-card, .pipeline-card { min-width: 0; }
}

@media (max-width: 767px) {
  .overview { gap: var(--space-lg); }
  .kpi-row { grid-template-columns: 1fr; gap: var(--space-md); }
  .kpi-card { padding: 16px; }
  .section-header { align-items: flex-start; flex-direction: column; gap: 12px; }
  .main-chart { height: 320px; }
  .detail-row { flex-direction: column; gap: 2px; }
}
</style>
