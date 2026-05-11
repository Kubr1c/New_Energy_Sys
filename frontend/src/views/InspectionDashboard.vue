<template>
  <div class="inspection">
    <PageState
      v-if="loading && !rawData.length"
      type="loading"
      title="正在加载预测验收数据"
      message="正在读取实际功率与预测功率对比数据。"
    />
    <PageState
      v-else-if="error && !rawData.length"
      type="error"
      title="预测验收数据加载失败"
      :message="error.message"
      retryable
      @retry="loadData"
    />
    <PageState
      v-else-if="!rawData.length && !loading"
      type="empty"
      title="暂无预测验收数据"
      message="当前日期区间无预测数据，请调整日期范围或检查后端数据。"
    />
    <template v-else>
      <div class="top-bar glass-panel">
        <div class="top-bar-left">
          <h2>预测结果验收控制台</h2>
          <span class="model-version" v-if="selectedExperimentLabel">{{ selectedExperimentLabel }}</span>
        </div>
        <div class="top-bar-right">
          <span class="range-hint">{{ dateRangeText }}</span>
        </div>
      </div>

      <div class="main-row">
        <aside class="left-panel glass-card">
          <div class="filter-stack">
            <div class="control-group">
              <label class="ctrl-label">日期</label>
              <div class="date-row">
                <el-button size="small" class="date-nav-btn" @click="goDate(-1)" :disabled="isAtMinDate" title="前一天">
                  <el-icon><ArrowLeft /></el-icon>
                </el-button>
                <el-date-picker
                  v-model="selectedDate"
                  type="date"
                  size="small"
                  :disabled-date="disableDate"
                  value-format="YYYY-MM-DD"
                  format="YYYY-MM-DD"
                  class="date-picker"
                  placeholder="选择日期"
                />
                <el-button size="small" class="date-nav-btn" @click="goDate(1)" :disabled="isAtMaxDate" title="后一天">
                  <el-icon><ArrowRight /></el-icon>
                </el-button>
              </div>
            </div>

            <el-divider class="ctrl-divider" />

            <div class="control-group">
              <label class="ctrl-label">预测时长</label>
              <el-checkbox-group v-model="selectedHorizons" class="ctrl-checkbox-group">
                <el-checkbox v-for="h in availableHorizons" :key="h" :label="h" :value="h" size="small">
                  <span :style="{ color: horizonColor(h) }">t+{{ h }}h</span>
                </el-checkbox>
              </el-checkbox-group>
            </div>

            <el-divider class="ctrl-divider" />

            <div class="control-group experiment-group">
              <label class="ctrl-label">预测方案</label>
              <div class="experiment-scroll">
                <el-radio-group v-model="selectedExperiment" class="experiment-radio-group" size="small">
                  <el-radio
                    v-for="exp in availableExperiments"
                    :key="exp.id"
                    :value="exp.id"
                    :title="exp.id"
                  >
                    <span class="experiment-label">{{ experimentLabel(exp) }}</span>
                  </el-radio>
                </el-radio-group>
              </div>
            </div>
          </div>

          <div class="fixed-filter-footer">
            <el-divider class="ctrl-divider" />

            <div class="control-group">
              <label class="ctrl-label">展示粒度</label>
              <el-radio-group v-model="selectedGranularity" class="ctrl-radio-group" size="small">
                <el-radio value="hour" label="小时" />
                <el-radio value="day" label="日" />
                <el-radio value="3day" label="3 日" />
                <el-radio value="7day" label="7 日" />
              </el-radio-group>
            </div>

            <el-divider class="ctrl-divider" />

            <div class="control-group">
              <label class="ctrl-label">天气场景</label>
              <el-radio-group v-model="selectedScenarioMode" class="ctrl-radio-group" size="small">
                <el-radio value="all" label="全部场景" />
                <el-radio value="clear" label="晴天" />
                <el-radio value="mixed" label="多云" />
                <el-radio value="overcast" label="阴天" />
              </el-radio-group>
            </div>
          </div>
        </aside>

        <div class="right-panel">
          <ChartCard :title="chartTitle">
            <p class="chart-note">{{ chartNote }}</p>
            <v-chart class="inspection-chart" :option="chartOption" theme="dark-tech" autoresize />
          </ChartCard>
        </div>
      </div>

      <div class="bottom-bar glass-panel">
        <div class="bottom-section-title">
          指标汇总
          <span class="section-hint" v-if="mainHorizon !== null">基于 {{ horizonLabel(mainHorizon) }} 日间数据</span>
        </div>

        <section
          v-for="group in metricGroups"
          :key="group.title"
          class="metric-section"
        >
          <h3>{{ group.title }}</h3>
          <div class="inspection-metric-grid">
            <div
              v-for="item in group.items"
              :key="item.key || item.label"
              class="inspection-metric-card glass-card"
            >
              <div class="metric-value-full">{{ item.value }}</div>
              <div class="metric-label-full">{{ item.label }}</div>
            </div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, MarkAreaComponent, MarkLineComponent, TitleComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import ChartCard from '../components/ChartCard.vue'
import PageState from '../components/PageState.vue'
import { buildInspectionChart } from '../charts/inspectionCharts'
import { fetchInspectionMetadata, fetchInspectionData } from '../services/inspectionService'
import { normalizeApiError } from '../utils/api'
import { experimentLabel } from '../utils/displayLabels'

use([CanvasRenderer, LineChart, BarChart, TooltipComponent, LegendComponent, GridComponent, DataZoomComponent, MarkAreaComponent, MarkLineComponent, TitleComponent])

const HORIZON_COLORS = { 1: '#2ee88b', 6: '#38bdf8', 24: '#ff7a7a' }
const HORIZON_LABELS = { 1: 't+1h', 6: 't+6h', 24: 't+24h' }

const loading = ref(false)
const error = ref(null)
const rawData = ref([])
const dailySummary = ref({})
const selectedDate = ref('')
const selectedHorizons = ref([1, 6, 24])
const selectedExperiment = ref('stage5')
const selectedGranularity = ref('hour')
const selectedScenarioMode = ref('all')
const availableHorizons = ref([1, 6, 24])
const availableExperiments = ref([{ id: 'stage5', model_name: 'LightGBM', feature_set: 'full_features' }])
const dateMin = ref('')
const dateMax = ref('')

const chartOption = computed(() => rawData.value.length ? buildInspectionChart(rawData.value, {
  horizons: selectedHorizons.value,
  experiment: selectedExperiment.value,
  scenarioMode: selectedScenarioMode.value,
  granularity: selectedGranularity.value,
  dailySummary: dailySummary.value,
}) : {})

const mainHorizon = computed(() => selectedHorizons.value[0] || 6)
const selectedExperimentLabel = computed(() => {
  const exp = availableExperiments.value.find(item => item.id === selectedExperiment.value)
  return experimentLabel(exp || selectedExperiment.value)
})
const queryRange = computed(() => {
  if (!selectedDate.value) return null
  const rangeDays = selectedGranularity.value === 'hour'
    ? 3
    : selectedGranularity.value === '7day'
      ? 42
      : 30
  const label = selectedGranularity.value === 'hour'
    ? '小时粒度时间序列'
    : selectedGranularity.value === 'day'
      ? '日粒度时间序列'
      : `${selectedGranularity.value === '3day' ? '3 日' : '7 日'}连续窗口时间序列`
  return buildQueryRange(selectedDate.value, rangeDays, label)
})
const chartTitle = computed(() => {
  if (selectedGranularity.value === 'hour') return '实际功率与预测功率对比'
  if (selectedGranularity.value === 'day') return '实际日发电量与预测日发电量对比'
  return `实际发电量与预测发电量对比（${selectedGranularity.value === '3day' ? '3 日' : '7 日'}窗口）`
})
const chartNote = computed(() => {
  if (selectedGranularity.value === 'hour') return '下方柱状图为预测误差，红色表示预测偏高，蓝色表示预测偏低。天气场景通过背景色区分。'
  return '下方柱状图为发电量误差，红色表示预测偏高，蓝色表示预测偏低。晴天、多云、阴天同时使用高对比颜色和不同线型区分。'
})
const dateRangeText = computed(() => {
  if (!queryRange.value) return ''
  return `${queryRange.value.start} ~ ${queryRange.value.displayEnd}（${queryRange.value.label}）`
})
const isAtMinDate = computed(() => Boolean(dateMin.value && selectedDate.value <= dateMin.value))
const isAtMaxDate = computed(() => Boolean(dateMax.value && selectedDate.value >= dateMax.value))
const horizonMetrics = computed(() => computeMetrics(rawData.value, dailySummary.value, mainHorizon.value, selectedExperiment.value))
const persistenceMetrics = computed(() => computePersistenceMetrics(rawData.value, mainHorizon.value, selectedExperiment.value))
const dailyEnergyMetrics = computed(() => computeDailyEnergyMetrics(dailySummary.value, rawData.value, mainHorizon.value, selectedExperiment.value))
const improvementRatio = computed(() => {
  const pRmse = persistenceMetrics.value.rmse
  const hRmse = horizonMetrics.value.rmse
  return pRmse > 0 ? ((1 - hRmse / pRmse) * 100).toFixed(1) : '-'
})
const metricGroups = computed(() => {
  const h = horizonMetrics.value
  const d = dailyEnergyMetrics.value
  const p = persistenceMetrics.value
  const hl = horizonLabel(mainHorizon.value)
  return [
    {
      title: '预测误差类',
      items: [
        { key: 'mae', label: `${hl} MAE`, value: formatMetric(h.mae, 4) },
        { key: 'rmse', label: `${hl} RMSE`, value: formatMetric(h.rmse, 4) },
        { key: 'nrmse', label: `${hl} nRMSE`, value: formatMetric(h.nrmse, 4) },
        { key: 'bias', label: `${hl} Bias`, value: formatMetric(h.bias, 4) },
      ],
    },
    {
      title: '发电量类',
      items: [
        { key: 'actual_kwh', label: selectedGranularity.value === 'hour' ? '当前范围实际发电量' : '图表范围实际发电量', value: `${formatMetric(d.actualKwh, 2)} kWh` },
        { key: 'pred_kwh', label: selectedGranularity.value === 'hour' ? '当前范围预测发电量' : '图表范围预测发电量', value: `${formatMetric(d.predKwh, 2)} kWh` },
        { key: 'energy_bias', label: '当前范围误差率', value: `${formatMetric(d.errorPct, 2)}%` },
      ],
    },
    {
      title: '基准对比类',
      items: [
        { key: 'persistence_rmse', label: '持续性基准 RMSE', value: formatMetric(p.rmse, 4) },
        { key: 'improvement', label: '相对基准提升率', value: `${improvementRatio.value}%` },
      ],
    },
  ]
})

function horizonColor(h) { return HORIZON_COLORS[h] || '#999' }
function horizonLabel(h) { return HORIZON_LABELS[h] || `t+${h}h` }
function fmtDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function parseDate(value) {
  return new Date(`${value}T00:00:00`)
}
function addDays(value, delta) {
  const d = value instanceof Date ? new Date(value) : parseDate(value)
  d.setDate(d.getDate() + delta)
  return d
}
function buildQueryRange(centerDate, rangeDays, label) {
  const center = parseDate(centerDate)
  const halfBefore = Math.floor((rangeDays - 1) / 2)
  let start = addDays(center, -halfBefore)
  let displayEnd = addDays(start, rangeDays - 1)

  // dateMin/dateMax 来自后端元数据，代表当前预测验收数据的可用日期边界。
  // 长粒度靠近边界时要整体平移，而不是简单截断到只剩一个窗口。
  if (dateMin.value && start < parseDate(dateMin.value)) {
    start = parseDate(dateMin.value)
    displayEnd = addDays(start, rangeDays - 1)
  }
  if (dateMax.value && displayEnd > parseDate(dateMax.value)) {
    displayEnd = parseDate(dateMax.value)
    start = addDays(displayEnd, -(rangeDays - 1))
    if (dateMin.value && start < parseDate(dateMin.value)) start = parseDate(dateMin.value)
  }

  return {
    start: fmtDate(start),
    displayEnd: fmtDate(displayEnd),
    endExclusive: fmtDate(addDays(displayEnd, 1)),
    label,
  }
}
function formatMetric(value, digits) {
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(digits) : '-'
}
function computeMetrics(data, summary, horizon, experiment) {
  const day = data.filter(row => row.horizon_hours === horizon && row.experiment === experiment && row.solar_elevation_deg != null && row.solar_elevation_deg > 5)
  if (!day.length) {
    const dailyRows = Object.values(summary || {})
      .map(item => item.experiments?.[experiment]?.[String(horizon)])
      .filter(Boolean)
    if (!dailyRows.length) return { rmse: 0, mae: 0, bias: 0, nrmse: 0, count: 0 }
    const avg = key => {
      const values = dailyRows.map(item => Number(item[key])).filter(Number.isFinite)
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0
    }
    return { rmse: avg('rmse_kw'), mae: avg('mae_kw'), bias: avg('bias_kw'), nrmse: 0, count: dailyRows.length }
  }
  const errors = day.map(row => row.error_kw ?? (row.prediction_kw - row.actual_kw))
  const actuals = day.map(row => row.actual_kw)
  const n = errors.length
  const rmse = Math.sqrt(errors.reduce((sum, value) => sum + value * value, 0) / n)
  const mae = errors.reduce((sum, value) => sum + Math.abs(value), 0) / n
  const bias = errors.reduce((sum, value) => sum + value, 0) / n
  const meanActual = actuals.reduce((sum, value) => sum + value, 0) / actuals.length
  return { rmse, mae, bias, nrmse: meanActual > 0 ? rmse / meanActual : 0, count: n }
}
function computePersistenceMetrics(data, horizon, experiment) {
  const filtered = data.filter(row => row.horizon_hours === horizon && row.experiment === experiment && row.solar_elevation_deg != null && row.solar_elevation_deg > 5)
  if (!filtered.length) return { rmse: 0 }
  const errors = filtered.map(row => (row.persistence_origin_kw ?? 0) - row.actual_kw)
  return { rmse: Math.sqrt(errors.reduce((sum, value) => sum + value * value, 0) / errors.length) }
}
function computeDailyEnergyMetrics(summary, data, horizon, experiment) {
  let totalActual = 0
  let totalPred = 0
  let matchedDays = 0

  // 优先读取后端 daily_summary 中“预测方案 + 预测时长”的组合结果。
  // 这个结构能避免把不同实验或不同时长的预测值混算到同一张答辩指标卡里。
  Object.values(summary || {}).forEach(day => {
    const actual = Number(day.daily_actual_kwh ?? 0)
    const pred = Number(day.experiments?.[experiment]?.[String(horizon)]?.daily_pred_kwh)
    if (Number.isFinite(pred)) {
      totalActual += actual
      totalPred += pred
      matchedDays += 1
    }
  })

  if (!matchedDays) {
    const grouped = new Map()
    data.forEach(row => {
      if (row.experiment !== experiment || row.horizon_hours !== horizon) return
      const date = String(row.valid_time || row.valid_date || '').slice(0, 10)
      if (!date) return
      if (!grouped.has(date)) grouped.set(date, { actual: 0, pred: 0 })
      const day = grouped.get(date)
      day.actual += Number(row.actual_kw ?? 0)
      day.pred += Number(row.prediction_kw ?? 0)
    })
    grouped.forEach(day => {
      totalActual += day.actual
      totalPred += day.pred
      matchedDays += 1
    })
  }

  const errorPct = totalActual > 0 ? ((totalPred - totalActual) / totalActual) * 100 : 0
  return { actualKwh: totalActual, predKwh: totalPred, errorPct, matchedDays }
}
function goDate(delta) {
  if (!selectedDate.value) return
  const d = new Date(selectedDate.value)
  d.setDate(d.getDate() + delta)
  selectedDate.value = fmtDate(d)
}
function disableDate(time) {
  if (!dateMin.value || !dateMax.value) return false
  const value = fmtDate(time)
  return value < dateMin.value || value > dateMax.value
}
async function loadMetadata() {
  const meta = await fetchInspectionMetadata()
  dateMin.value = meta.date_min || ''
  dateMax.value = meta.date_max || ''
  if (Array.isArray(meta.horizons) && meta.horizons.length) availableHorizons.value = meta.horizons
  if (Array.isArray(meta.experiments) && meta.experiments.length) availableExperiments.value = meta.experiments
  if (!selectedDate.value) selectedDate.value = '2022-08-10'
  if (!availableExperiments.value.some(exp => exp.id === selectedExperiment.value)) selectedExperiment.value = availableExperiments.value[0]?.id || 'stage5'
}
async function loadData() {
  if (!selectedDate.value || !queryRange.value) return
  loading.value = true
  error.value = null
  try {
    const range = queryRange.value
    const queryHorizons = selectedGranularity.value === 'hour'
      ? (selectedHorizons.value.length ? selectedHorizons.value : [mainHorizon.value])
      : [mainHorizon.value]
    const params = {
      start: range.start,
      end: range.endExclusive,
      horizons: queryHorizons.join(','),
      experiments: selectedExperiment.value,
    }
    if (selectedGranularity.value !== 'hour') params.granularity = 'day'
    const result = await fetchInspectionData(params)
    rawData.value = result.data || []
    dailySummary.value = result.daily_summary || {}
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
    rawData.value = []
    dailySummary.value = {}
  } finally {
    loading.value = false
  }
}

watch([selectedDate, selectedHorizons, selectedExperiment, selectedGranularity], loadData)
onMounted(async () => {
  try {
    await loadMetadata()
    await loadData()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  }
})
</script>

<style scoped>
.inspection { display: flex; flex-direction: column; gap: var(--space-lg); }
.top-bar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-md); padding: var(--space-md) var(--space-lg); }
.top-bar-left { display: flex; align-items: baseline; gap: var(--space-md); }
.top-bar-left h2 { color: var(--text-primary); font-size: 16px; font-weight: 700; line-height: 1.25; }
.model-version { color: var(--accent-cyan); font-size: 12px; background: rgba(0, 212, 255, 0.1); border: 1px solid rgba(0, 212, 255, 0.2); border-radius: var(--radius-full); padding: 2px 10px; }
.range-hint { color: var(--text-secondary); font-size: 12px; white-space: nowrap; }
.main-row { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: var(--space-lg); align-items: start; }
.left-panel {
  display: flex;
  flex-direction: column;
  gap: 0;
  max-height: calc(100vh - 168px);
  min-height: 560px;
  min-width: 0;
  padding: var(--space-md);
  position: sticky;
  top: 96px;
}
.filter-stack { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; }
.fixed-filter-footer { flex: 0 0 auto; }
.control-group { display: flex; flex-direction: column; gap: var(--space-sm); padding: var(--space-sm) 0; }
.ctrl-label { color: var(--text-secondary); font-size: 12px; font-weight: 700; }
.ctrl-divider { margin: 2px 0; border-color: var(--border-glass); }
.date-row { display: flex; align-items: center; gap: 6px; }
.date-nav-btn { flex: 0 0 auto; padding: 6px 8px; font-size: 14px; line-height: 1; }
.date-picker { flex: 1; min-width: 0; }
.date-picker :deep(.el-input__wrapper) { padding: 0 8px; }
.date-picker :deep(.el-input__inner) { font-size: 12px; text-align: center; }
.ctrl-checkbox-group,
.ctrl-radio-group { display: flex; gap: 8px; flex-wrap: wrap; }
.experiment-group { flex: 1 1 auto; min-height: 120px; }
.experiment-scroll {
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: var(--radius-md);
  flex: 1 1 auto;
  min-height: 0;
  max-height: 260px;
  overflow-y: auto;
  padding: 6px 8px;
}
.experiment-radio-group { display: flex; flex-direction: column; gap: 6px; align-items: stretch; width: 100%; }
.experiment-radio-group :deep(.el-radio) {
  align-items: flex-start;
  height: auto;
  margin-right: 0;
  min-height: 28px;
  white-space: normal;
}
.experiment-radio-group :deep(.el-radio__label) { min-width: 0; line-height: 1.4; padding-left: 8px; }
.experiment-label {
  color: var(--text-primary);
  display: block;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.right-panel { min-width: 0; display: flex; flex-direction: column; }
.inspection-chart { height: 540px; width: 100%; }
.chart-note { color: var(--text-secondary); font-size: 12px; line-height: 1.6; margin-bottom: var(--space-md); }
.bottom-bar { display: flex; flex-direction: column; gap: var(--space-lg); padding: var(--space-lg); }
.bottom-section-title { color: var(--text-primary); font-size: 16px; font-weight: 700; }
.section-hint { color: var(--text-secondary); font-size: 12px; font-weight: 400; margin-left: 8px; }
.metric-section { display: flex; flex-direction: column; gap: var(--space-md); }
.metric-section h3 { color: var(--text-primary); font-size: 14px; font-weight: 700; }
.inspection-metric-grid {
  display: grid;
  gap: var(--space-md);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.inspection-metric-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  justify-content: center;
  min-height: 104px;
  min-width: 0;
  padding: 18px 20px;
}
.metric-value-full {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  overflow: visible;
  white-space: normal;
  word-break: keep-all;
}
.metric-label-full {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

@media (max-width: 1199px) {
  .main-row { grid-template-columns: 1fr; }
  .left-panel {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-height: none;
    min-height: 0;
    position: static;
  }
  .filter-stack,
  .fixed-filter-footer { min-height: 0; }
  .experiment-scroll { max-height: 220px; }
  .ctrl-divider { display: none; }
  .inspection-metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}

@media (max-width: 767px) {
  .top-bar { flex-direction: column; align-items: flex-start; }
  .main-row { gap: var(--space-md); }
  .left-panel { grid-template-columns: 1fr; }
  .inspection-chart { height: 420px; }
  .bottom-bar { padding: var(--space-md); }
  .inspection-metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .inspection-metric-card { min-height: 96px; padding: 14px; }
  .metric-value-full { font-size: 17px; }
}
</style>
