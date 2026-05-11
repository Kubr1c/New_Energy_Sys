<template>
  <div class="overview">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载系统总览"
      message="正在读取站点配置、模型指标和预测曲线数据。"
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
      message="后端未返回站点配置、模型指标或预测曲线，请检查展示数据是否已经生成。"
      retryable
      @retry="loadData"
    />
    <template v-else>

      <section class="overview-intro glass-panel">
        <div>
          <span class="intro-kicker">项目总览</span>
          <h1>储能调度多收益情景量化展示系统</h1>
          <p>系统面向光伏配储调度场景，集成天气驱动仿真、退化成本评估与多收益情景量化展示，用于辅助分析储能调度在不同经济假设下的净增量收益表现。所有收益均为相对无储能基准的仿真增量，电价来自 OPSD 映射或项目代理场景。</p>
        </div>
        <div class="entry-grid" aria-label="功能入口">
          <button v-for="entry in featureEntries" :key="entry.path" type="button" class="entry-btn" @click="$router.push(entry.path)">
            <el-icon :size="18"><component :is="entry.icon" /></el-icon>
            <span>{{ entry.label }}</span>
          </button>
        </div>
      </section>

      <MetricGrid :items="kpiCards" min-width="220px" />

      <div class="main-row">
        <ChartCard title="预测效果概览">
          <template #actions>
            <div class="overview-chart-actions">
              <el-button data-testid="overview-prev-day" size="small" class="overview-date-nav" :disabled="isAtMinDate" @click="goDate(-1)">
                <el-icon><ArrowLeft /></el-icon>
              </el-button>
              <el-date-picker
                v-model="selectedDate"
                type="date"
                size="small"
                :disabled-date="disableDate"
                value-format="YYYY-MM-DD"
                format="YYYY-MM-DD"
                class="overview-date-picker"
                @change="loadInspectionData"
              />
              <el-button data-testid="overview-next-day" size="small" class="overview-date-nav" :disabled="isAtMaxDate" @click="goDate(1)">
                <el-icon><ArrowRight /></el-icon>
              </el-button>

              <el-checkbox-group v-model="selectedHorizons" class="overview-horizons" @change="handleHorizonsChange">
                <el-checkbox v-for="h in availableHorizons" :key="h" :label="h" :value="h" size="small">
                  <span :style="{ color: horizonColor(h) }">t+{{ h }}h</span>
                </el-checkbox>
              </el-checkbox-group>

              <el-select v-model="selectedScenarioMode" size="small" class="overview-select">
                <el-option v-for="option in scenarioOptions" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
            </div>
          </template>

          <PageState v-if="inspectionError" type="error" title="预测曲线加载失败" :message="inspectionError.message" retryable @retry="loadInspectionData" />
          <PageState v-else-if="!inspectionRows.length" type="empty" title="当前窗口暂无预测曲线" message="请调整日期、预测时长或天气场景。" />
          <v-chart v-else class="main-chart overview-inspection-chart" :option="predChartOption" theme="dark-tech" autoresize />
        </ChartCard>

        <div class="summary-column">
          <section class="glass-card summary-card">
            <h3>预测输入品质</h3>
            <div class="detail-row"><span>当前展示模型</span><strong class="accent">{{ currentDisplayModelName }}</strong></div>
            <div class="detail-row"><span>测试集最优模型</span><strong>{{ bestTestModelName }}</strong></div>
            <div class="detail-row"><span>测试集 nRMSE</span><strong>{{ metricText(mainMetric.nrmse_capacity) }}</strong></div>
            <div class="detail-row"><span>数据记录</span><strong>{{ Number.isFinite(Number(recordCount)) ? Number(recordCount).toLocaleString('zh-CN') : recordCount }}</strong></div>
            <div class="detail-row"><span>特征字段</span><strong>{{ featureCount }}</strong></div>
            <div class="detail-row"><span>地理位置</span><strong>{{ locationText }}</strong></div>
          </section>

          <section class="glass-card summary-card">
            <h3>调度数据口径</h3>
            <div class="status-line good">数据来源：PVDAQ / NSRDB / OPSD / Open-Meteo 公开数据</div>
            <div class="status-line good">仿真周期：2020-01 ~ 2022-12，小时粒度</div>
            <div class="status-line muted">所有收益为仿真增量收益。Rawhide 相关为公开容量参数参照场景，不构成真实电站运行或市场结算结果。</div>
          </section>

          <section class="glass-card pipeline-card">
            <h3>功能流程</h3>
            <div class="pipeline-grid">
              <span v-for="item in pipelineStages" :key="item.name">{{ item.name }}</span>
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
import { BarChart, LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageState from '../components/PageState.vue'
import { buildInspectionChart } from '../charts/inspectionCharts'
import { fetchInspectionData, fetchInspectionMetadata } from '../services/inspectionService'
import { fetchOverviewBundle } from '../services/overviewService'
import { fetchModelComparison } from '../services/modelService'
import { fetchShowcaseScenarios, fetchShowcaseSummary } from '../services/dispatchService'
import { normalizeApiError } from '../utils/api'
import { modelLabel } from '../utils/displayLabels'

use([CanvasRenderer, LineChart, BarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, DataZoomComponent, MarkAreaComponent, MarkLineComponent])

const DEFAULT_OVERVIEW_DATE = '2022-08-10'
const HORIZON_COLORS = { 1: '#00ff88', 6: '#00bfff', 24: '#ff6b6b' }

const siteConfig = ref({})
const mainMetrics = ref([])
const quality = ref({})
const dispatchMetrics = ref([])
const modelComparisonRows = ref([])
const inspectionRows = ref([])
const selectedDate = ref('')
const selectedHorizons = ref([1, 6, 24])
const selectedExperiment = ref('stage5')
const selectedScenarioMode = ref('all')
const availableHorizons = ref([1, 6, 24])
const availableExperiments = ref([{ id: 'stage5', model_name: 'LightGBM', feature_set: 'full_features' }])
const dateMin = ref('')
const dateMax = ref('')
const showcaseScenarios = ref([])
const showcaseReport = ref(null)
const loading = ref(false)
const error = ref(null)
const inspectionError = ref(null)

const scenarioOptions = [
  { value: 'all', label: '全部场景' },
  { value: 'clear', label: '仅晴天' },
  { value: 'mixed', label: '仅多云' },
  { value: 'overcast', label: '仅阴天' },
]

const mainMetric = computed(() => mainMetrics.value.find(row => row.split === 'test') || {})
const bestTestMetric = computed(() => [...modelComparisonRows.value].filter(row => row.split === 'test').sort((a, b) => Number(a.nrmse_capacity || Infinity) - Number(b.nrmse_capacity || Infinity))[0] || {})
const hasOverviewData = computed(() =>
  Boolean(siteConfig.value?.site) ||
  mainMetrics.value.length > 0 ||
  modelComparisonRows.value.length > 0 ||
  inspectionRows.value.length > 0 ||
  dispatchMetrics.value.length > 0
)
const predChartOption = computed(() => buildInspectionChart(inspectionRows.value, {
  horizons: selectedHorizons.value,
  experiment: selectedExperiment.value,
  scenarioMode: selectedScenarioMode.value,
}))
const validTimeCount = computed(() => new Set(inspectionRows.value.map(row => row.valid_time)).size)
const primaryHorizon = computed(() => selectedHorizons.value[0] || availableHorizons.value[0] || 1)
const primaryHorizonLabel = computed(() => `t+${primaryHorizon.value}h`)
const primaryMetrics = computed(() => computeDaytimeMetrics(inspectionRows.value, primaryHorizon.value, selectedExperiment.value))
const currentDisplayModelName = computed(() => modelLabel(mainMetric.value.model || availableExperiments.value.find(exp => exp.id === selectedExperiment.value)?.model_name || 'LightGBM'))
const bestTestModelName = computed(() => modelLabel(bestTestMetric.value.model || 'TCN'))
const featureCount = computed(() => quality.value?.schema?.column_count ?? quality.value?.columns?.total ?? '数据缺失')
const recordCount = computed(() => quality.value?.rows?.final_cleaned ?? inspectionRows.value.length)
const rollingMetric = computed(() => dispatchMetrics.value.find(item => item.scenario === 'rolling_optimization') || {})
const dispatchRevenue = computed(() => formatCurrency(rollingMetric.value.incremental_revenue_eur))
const overviewInsight = computed(() => {
  const totalCount = showcaseScenarios.value.length
  const posCount = positiveScenarioCount.value
  const bestName = bestScenario.value?.scenario_name || '—'
  const bestNet = bestScenario.value ? formatCurrency(bestScenario.value.net_incremental_revenue_eur) : '—'
  const nrmse = metricText(mainMetric.value.nrmse_capacity)
  return {
    title: `在 ${totalCount} 个收益情景中，${posCount} 个取得正净增量，最优情景"${bestName}"净增量 ${bestNet}。`,
    tone: posCount > 0 ? 'positive' : 'warning',
    items: [
      `预测品质：${currentDisplayModelName.value} 测试集 nRMSE ${nrmse}，作为调度前置环节提供光伏出力估算。`,
      `调度基准：基准代理电价下纯套利净增量 ${baselineNet.value !== null ? formatCurrency(baselineNet.value) : '数据缺失'}，验证"套利不抵退化"结论。`,
      `正路径：容量价值叠加、价差放大或电池成本改善条件下可实现正净增量。Rawhide 相关内容为公开容量参数参照场景，不构成实测电站运行数据。`,
    ],
  }
})
const locationText = computed(() => {
  const site = siteConfig.value?.site
  return site ? `${site.latitude}°N, ${Math.abs(site.longitude)}°W` : '-'
})
const positiveScenarioCount = computed(() => showcaseScenarios.value.filter(s => Number(s.net_incremental_revenue_eur) > 0).length)
const bestScenario = computed(() => [...showcaseScenarios.value].sort((a, b) => Number(b.net_incremental_revenue_eur) - Number(a.net_incremental_revenue_eur))[0])
const baselineScenario = computed(() => showcaseScenarios.value.find(s => s.scenario_type === 'baseline'))
const baselineNet = computed(() => baselineScenario.value ? Number(baselineScenario.value.net_incremental_revenue_eur) : null)
// Policy distillation accuracy — fixed from task_report_policy_distillation_replay_2026-05-10.md
const POLICY_DISTILLATION_ACCURACY = 0.9908

const kpiCards = computed(() => [
  { label: '最优情景净增量', value: bestScenario.value ? formatCurrency(bestScenario.value.net_incremental_revenue_eur) : '数据缺失', icon: 'Coin', gradient: 'var(--gradient-cyan)' },
  { label: '正净增量情景数', value: `${positiveScenarioCount.value} / ${showcaseScenarios.value.length}`, icon: 'DataAnalysis', gradient: 'var(--gradient-green)' },
  { label: '基准纯套利结论', value: baselineNet.value !== null && baselineNet.value < 0 ? '套利不抵退化成本' : '待确认', icon: 'Warning', gradient: 'var(--gradient-orange)' },
  { label: '策略蒸馏准确率', value: POLICY_DISTILLATION_ACCURACY, icon: 'Select', gradient: 'var(--gradient-purple)' },
  { label: '预测输入模型', value: currentDisplayModelName.value, icon: 'TrendCharts', gradient: 'var(--gradient-cyan)' },
])
const pipelineStages = [
  { name: '公开数据' },
  { name: '光伏预测' },
  { name: '滚动调度' },
  { name: '退化修正' },
  { name: '多收益情景' },
  { name: '策略展示' },
]
const featureEntries = [
  { path: '/dispatch', label: '储能调度', icon: 'Setting' },
  { path: '/data', label: '数据管理', icon: 'DataLine' },
  { path: '/models', label: '预测分析', icon: 'TrendCharts' },
  { path: '/inspect', label: '预测验收', icon: 'Monitor' },
]
const dateRangeText = computed(() => {
  if (!selectedDate.value) return '-'
  return `${addDays(selectedDate.value, -1)} ~ ${addDays(selectedDate.value, 1)}`
})
const isAtMinDate = computed(() => Boolean(dateMin.value && selectedDate.value <= dateMin.value))
const isAtMaxDate = computed(() => Boolean(dateMax.value && selectedDate.value >= dateMax.value))

function horizonColor(horizon) { return HORIZON_COLORS[horizon] || '#8a92a6' }
function metricText(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(4) : '数据缺失'
}
function formatCurrency(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${n.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })} 欧元` : '数据缺失'
}
function fmtDate(date) {
  const d = typeof date === 'string' ? new Date(date) : date
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function addDays(dateStr, delta) {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + delta)
  return fmtDate(d)
}
function clampDate(dateStr) {
  if (dateMin.value && dateStr < dateMin.value) return dateMin.value
  if (dateMax.value && dateStr > dateMax.value) return dateMax.value
  return dateStr
}
function disableDate(time) {
  if (!dateMin.value || !dateMax.value) return false
  const value = fmtDate(time)
  return value < dateMin.value || value > dateMax.value
}
function goDate(delta) {
  if (!selectedDate.value) return
  selectedDate.value = clampDate(addDays(selectedDate.value, delta))
  loadInspectionData()
}
function handleHorizonsChange(value) {
  if (!value.length && availableHorizons.value.length) selectedHorizons.value = [availableHorizons.value[0]]
  loadInspectionData()
}
function computeDaytimeMetrics(rows, horizon, experiment) {
  const daytime = rows.filter(row => row.horizon_hours === horizon && row.experiment === experiment && row.solar_elevation_deg != null && Number(row.solar_elevation_deg) > 5)
  if (!daytime.length) return { mae: null, rmse: null, bias: null }
  const errors = daytime.map(row => Number(row.error_kw ?? (row.prediction_kw - row.actual_kw))).filter(Number.isFinite)
  if (!errors.length) return { mae: null, rmse: null, bias: null }
  const mae = errors.reduce((sum, value) => sum + Math.abs(value), 0) / errors.length
  const rmse = Math.sqrt(errors.reduce((sum, value) => sum + value * value, 0) / errors.length)
  const bias = errors.reduce((sum, value) => sum + value, 0) / errors.length
  return { mae, rmse, bias }
}
async function loadMetadata() {
  const meta = await fetchInspectionMetadata()
  dateMin.value = meta.date_min || ''
  dateMax.value = meta.date_max || ''
  availableHorizons.value = Array.isArray(meta.horizons) && meta.horizons.length ? meta.horizons : [1, 6, 24]
  availableExperiments.value = Array.isArray(meta.experiments) && meta.experiments.length ? meta.experiments : [{ id: 'stage5', model_name: 'LightGBM', feature_set: 'full_features' }]
  selectedHorizons.value = selectedHorizons.value.filter(h => new Set(availableHorizons.value).has(h))
  if (!selectedHorizons.value.length) selectedHorizons.value = availableHorizons.value.slice(0, 3)
  const experimentIds = new Set(availableExperiments.value.map(exp => exp.id))
  if (!experimentIds.has(selectedExperiment.value)) selectedExperiment.value = experimentIds.has('stage5') ? 'stage5' : availableExperiments.value[0].id
  selectedDate.value = clampDate(DEFAULT_OVERVIEW_DATE)
}
async function loadInspectionData() {
  if (!selectedDate.value || !selectedExperiment.value || !selectedHorizons.value.length) return
  inspectionError.value = null
  try {
    const result = await fetchInspectionData({
      start: addDays(selectedDate.value, -1),
      end: addDays(selectedDate.value, 2),
      horizons: selectedHorizons.value.join(','),
      experiments: selectedExperiment.value,
    })
    inspectionRows.value = result.data || []
  } catch (e) {
    inspectionError.value = e.normalized || normalizeApiError(e)
    inspectionRows.value = []
  }
}
async function loadData() {
  loading.value = true
  error.value = null
  inspectionError.value = null
  try {
    const [bundle, modelData, scenarios, summary] = await Promise.all([
      fetchOverviewBundle(),
      fetchModelComparison(),
      fetchShowcaseScenarios().catch(() => []),
      fetchShowcaseSummary().catch(() => null),
      loadMetadata().catch(() => {}),
    ])
    if (bundle) {
      siteConfig.value = bundle.siteConfig || {}
      mainMetrics.value = bundle.mainMetrics || []
      quality.value = bundle.quality || {}
      dispatchMetrics.value = bundle.dispatchMetrics || []
    }
    modelComparisonRows.value = [...(modelData?.tabularMetrics || []), ...(modelData?.deepLearningMetrics || [])]
    showcaseScenarios.value = Array.isArray(scenarios) ? scenarios : []
    showcaseReport.value = summary
    try {
      await loadInspectionData()
    } catch {
      // Inspection failure is already handled in loadInspectionData via inspectionError
    }
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
.overview-intro { align-items: center; display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 0.45fr); gap: var(--space-lg); padding: var(--space-xl); }
.intro-kicker { color: var(--accent-cyan); display: block; font-size: 12px; font-weight: 800; margin-bottom: 8px; }
.overview-intro h1 { color: var(--text-primary); font-size: 28px; line-height: 1.2; margin-bottom: 10px; }
.overview-intro p { color: var(--text-secondary); font-size: 14px; line-height: 1.8; }
.entry-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.entry-btn { align-items: center; background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-sm); color: var(--text-primary); cursor: pointer; display: flex; gap: 8px; min-height: 44px; padding: 10px 12px; }
.entry-btn:hover { border-color: var(--border-active); color: var(--accent-cyan); }
.main-row { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: var(--space-lg); }
.main-chart { height: 460px; width: 100%; }
.overview-chart-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; max-width: 780px; }
.overview-date-nav { width: 32px; padding: 6px; }
.overview-date-picker { width: 138px; }
.overview-horizons { display: flex; align-items: center; gap: 6px; }
.overview-select { width: 150px; }
.summary-column { display: flex; flex-direction: column; gap: var(--space-md); }
.summary-card,
.pipeline-card { padding: var(--space-lg); }
.summary-card h3,
.pipeline-card h3 { color: var(--accent-cyan); font-size: 14px; font-weight: 700; margin-bottom: 12px; }
.detail-row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--border-glass); }
.detail-row span { color: var(--text-secondary); font-size: 12px; }
.detail-row strong { color: var(--text-primary); font-size: 13px; text-align: right; }
.detail-row .accent { color: var(--accent-cyan); }
.status-line { border: 1px solid var(--border-glass); border-radius: var(--radius-sm); color: var(--text-primary); font-size: 12px; margin-bottom: 8px; padding: 8px 10px; }
.status-line.good { border-color: rgba(0, 245, 160, 0.2); }
.status-line.muted { color: var(--text-secondary); }
.pipeline-grid { display: grid; gap: 8px; }
.pipeline-grid span { color: var(--text-secondary); background: var(--bg-input); border-radius: var(--radius-sm); font-size: 12px; padding: 7px 9px; }

@media (max-width: 1199px) {
  .overview-intro { grid-template-columns: 1fr; }
  .main-row { grid-template-columns: 1fr; }
  .summary-column { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .overview-chart-actions { justify-content: flex-start; max-width: none; }
}

@media (max-width: 767px) {
  .overview-intro { padding: var(--space-lg); }
  .overview-intro h1 { font-size: 22px; }
  .entry-grid { grid-template-columns: 1fr; }
  .summary-column { grid-template-columns: 1fr; }
  .main-chart { height: 400px; }
  .overview-date-picker,
  .overview-select { width: min(100%, 160px); }
}
</style>
