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
      message="后端未返回站点配置、模型指标或预测曲线，请检查实验产物是否已生成。"
      retryable
      @retry="loadData"
    />
    <template v-else>
      <MetricGrid :items="kpiCards" min-width="220px" />

      <div class="main-row">
        <ChartCard title="PV 功率预测 vs 实际 — Prediction vs Actual">
          <template #actions>
            <el-select v-model="predDays" size="small" style="width: 96px" @change="loadPredictions">
              <el-option label="7 天" :value="168" />
              <el-option label="14 天" :value="336" />
              <el-option label="30 天" :value="720" />
            </el-select>
          </template>
          <v-chart class="main-chart" :option="predChartOption" theme="dark-tech" autoresize />
        </ChartCard>

        <div class="summary-column">
          <section class="glass-card summary-card">
            <h3>站点信息 Site Info</h3>
            <div class="detail-row"><span>Site</span><strong>PVDAQ System 10</strong></div>
            <div class="detail-row"><span>Location</span><strong>{{ locationText }}</strong></div>
            <div class="detail-row"><span>Period</span><strong>2020-01 ~ 2022-12</strong></div>
            <div class="detail-row"><span>Main Model</span><strong class="accent">LightGBM tuned</strong></div>
          </section>

          <section class="glass-card summary-card">
            <h3>运行摘要 Model Status</h3>
            <div class="status-line good">主模型测试集 nRMSE {{ metricText(mainMetric.nrmse_capacity) }}</div>
            <div class="status-line good">日间 nRMSE {{ metricText(mainMetric.daytime_nrmse_capacity) }}</div>
            <div class="status-line muted">预测曲线 {{ predictions.length.toLocaleString('zh-CN') }} 个时间点</div>
          </section>

          <section class="glass-card pipeline-card">
            <h3>技术路线 Pipeline</h3>
            <div class="pipeline-grid">
              <span v-for="stage in pipelineStages" :key="stage.id">{{ stage.id }} {{ stage.name }}</span>
            </div>
          </section>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageState from '../components/PageState.vue'
import { buildPredictionChartOption } from '../charts/overviewCharts'
import { fetchMainPredictions, fetchOverviewBundle } from '../services/overviewService'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, LineChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, DataZoomComponent])

const siteConfig = ref({})
const mainMetrics = ref([])
const predictions = ref([])
const predDays = ref(168)
const loading = ref(false)
const error = ref(null)

const mainMetric = computed(() => mainMetrics.value.find(row => row.split === 'test') || {})
const hasOverviewData = computed(() => Boolean(siteConfig.value?.site) || mainMetrics.value.length > 0 || predictions.value.length > 0)
const predChartOption = computed(() => buildPredictionChartOption(predictions.value))
const locationText = computed(() => {
  const site = siteConfig.value?.site
  if (!site) return '-'
  return `${site.latitude}°N, ${Math.abs(site.longitude)}°W`
})

const kpiCards = computed(() => [
  { label: 'PV 容量 Capacity', value: siteConfig.value?.site?.capacity_kw ? `${siteConfig.value.site.capacity_kw} kW` : '数据缺失', icon: 'Sunny', gradient: 'var(--gradient-orange)' },
  { label: '主模型 nRMSE', value: metricText(mainMetric.value.nrmse_capacity), icon: 'TrendCharts', gradient: 'var(--gradient-cyan)' },
  { label: '日间 nRMSE', value: metricText(mainMetric.value.daytime_nrmse_capacity), icon: 'Sunrise', gradient: 'var(--gradient-green)' },
  { label: '储能容量 Storage', value: siteConfig.value?.storage?.capacity_kwh ? `${siteConfig.value.storage.capacity_kwh} kWh` : '数据缺失', icon: 'Coin', gradient: 'var(--gradient-purple)' },
])

const pipelineStages = [
  { id: 'S1-S3', name: '数据工程' },
  { id: 'S4-S9', name: '预测建模' },
  { id: 'S10-S13', name: '调度治理' },
  { id: 'S14-S15', name: '增强分析' },
  { id: 'S17-S18', name: '退化与实站' },
]

function metricText(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(4) : '数据缺失'
}

async function loadPredictions() {
  try {
    predictions.value = await fetchMainPredictions(predDays.value)
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  }
}

async function loadData() {
  loading.value = true
  error.value = null
  try {
    const bundle = await fetchOverviewBundle()
    siteConfig.value = bundle.siteConfig
    mainMetrics.value = bundle.mainMetrics
    await loadPredictions()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.overview { display: flex; flex-direction: column; gap: var(--space-lg); }
.main-row { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: var(--space-lg); }
.main-chart { height: 380px; width: 100%; }
.summary-column { display: flex; flex-direction: column; gap: var(--space-md); }
.summary-card,
.pipeline-card { padding: var(--space-lg); }
.summary-card h3,
.pipeline-card h3 { color: var(--accent-cyan); font-size: 14px; font-weight: 700; margin-bottom: 12px; }
.detail-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid var(--border-glass);
}
.detail-row span { color: var(--text-secondary); font-size: 12px; }
.detail-row strong { color: var(--text-primary); font-size: 13px; text-align: right; }
.detail-row .accent { color: var(--accent-cyan); }
.status-line {
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-size: 12px;
  margin-bottom: 8px;
  padding: 8px 10px;
}
.status-line.good { border-color: rgba(0, 245, 160, 0.2); }
.status-line.muted { color: var(--text-secondary); }
.pipeline-grid { display: grid; gap: 8px; }
.pipeline-grid span {
  color: var(--text-secondary);
  background: var(--bg-input);
  border-radius: var(--radius-sm);
  font-size: 12px;
  padding: 7px 9px;
}

@media (max-width: 1199px) {
  .main-row { grid-template-columns: 1fr; }
  .summary-column { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
}

@media (max-width: 767px) {
  .summary-column { grid-template-columns: 1fr; }
  .main-chart { height: 320px; }
}
</style>
