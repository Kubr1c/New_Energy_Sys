<template>
  <div class="inspection">
    <!-- ===== Loading state ===== -->
    <PageState
      v-if="loading && !rawData.length"
      type="loading"
      title="正在加载预测验收数据"
      message="正在向后端请求预测对比数据，请稍候。"
    />
    <!-- ===== Error state ===== -->
    <PageState
      v-else-if="error && !rawData.length"
      type="error"
      title="预测验收数据加载失败"
      :message="error.message"
      retryable
      @retry="loadData"
    />
    <!-- ===== Empty state ===== -->
    <PageState
      v-else-if="!rawData.length && !loading"
      type="empty"
      title="暂无预测验收数据"
      message="当前日期区间无预测数据，请调整日期范围或检查后端数据。"
    />
    <!-- ===== Main content ===== -->
    <template v-else>
      <!-- ---- Top bar ---- -->
      <div class="top-bar glass-panel">
        <div class="top-bar-left">
          <h2>预测结果验收控制台</h2>
          <span class="model-version" v-if="metadata.model_version">
            Model: {{ metadata.model_version }}
          </span>
        </div>
        <div class="top-bar-right">
          <span class="range-hint">
            {{ dateRangeText }}
          </span>
        </div>
      </div>

      <!-- ---- Main row: left sidebar + right chart ---- -->
      <div class="main-row">
        <!-- Left panel — controls -->
        <aside class="left-panel glass-card">
          <!-- Date picker with navigation -->
          <div class="control-group">
            <label class="ctrl-label">日期</label>
            <div class="date-row">
              <el-button
                :icon="false"
                size="small"
                class="date-nav-btn"
                @click="goDate(-1)"
                :disabled="isAtMinDate"
                title="前一天"
              >
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
              <el-button
                :icon="false"
                size="small"
                class="date-nav-btn"
                @click="goDate(1)"
                :disabled="isAtMaxDate"
                title="后一天"
              >
                <el-icon><ArrowRight /></el-icon>
              </el-button>
            </div>
          </div>

          <el-divider class="ctrl-divider" />

          <!-- Horizon checkboxes -->
          <div class="control-group">
            <label class="ctrl-label">预测时长</label>
            <el-checkbox-group v-model="selectedHorizons" class="ctrl-checkbox-group">
              <el-checkbox
                v-for="h in availableHorizons"
                :key="h"
                :label="h"
                :value="h"
                size="small"
              >
                <span :style="{ color: horizonColor(h) }">t+{{ h }}h</span>
              </el-checkbox>
            </el-checkbox-group>
          </div>

          <el-divider class="ctrl-divider" />

          <!-- Experiment radio -->
          <div class="control-group">
            <label class="ctrl-label">实验分组</label>
            <el-radio-group v-model="selectedExperiment" class="ctrl-radio-group" size="small">
              <el-radio
                v-for="exp in availableExperiments"
                :key="exp"
                :value="exp"
                :label="exp"
              />
            </el-radio-group>
          </div>

          <el-divider class="ctrl-divider" />

          <!-- Granularity radio -->
          <div class="control-group">
            <label class="ctrl-label">采集粒度</label>
            <el-radio-group v-model="selectedGranularity" class="ctrl-radio-group" size="small">
              <el-radio value="hour" label="小时" />
              <el-radio value="day" label="日" />
              <el-radio value="3day" label="3日" />
              <el-radio value="7day" label="7日" />
            </el-radio-group>
          </div>

          <el-divider class="ctrl-divider" />

          <!-- Scenario display mode -->
          <div class="control-group">
            <label class="ctrl-label">天气场景</label>
            <el-radio-group v-model="selectedScenarioMode" class="ctrl-radio-group" size="small">
              <el-radio value="all" label="全部(背景)" />
              <el-radio value="clear" label="仅晴天" />
              <el-radio value="mixed" label="仅多云" />
              <el-radio value="overcast" label="仅阴天" />
            </el-radio-group>
          </div>
        </aside>

        <!-- Right panel — chart -->
        <div class="right-panel">
          <ChartCard title="预测对比">
            <v-chart
              class="inspection-chart"
              :option="chartOption"
              theme="dark-tech"
              autoresize
            />
          </ChartCard>
        </div>
      </div>

      <!-- ---- Bottom metrics ---- -->
      <div class="bottom-bar">
        <div class="bottom-section-title">
          指标汇总
          <span class="section-hint" v-if="mainHorizon !== null">
            (基于 {{ horizonLabel(mainHorizon) }} | 仅日间数据)
          </span>
        </div>
        <MetricGrid :items="metricCards" min-width="180px" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  MarkAreaComponent,
  MarkLineComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageState from '../components/PageState.vue'
import { buildInspectionChart } from '../charts/inspectionCharts'
import { fetchInspectionMetadata, fetchInspectionData } from '../services/inspectionService'
import { normalizeApiError } from '../utils/api'

// Register ECharts components
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkLineComponent,
])

// ======================================================================
// Constants
// ======================================================================
const HORIZON_COLORS = { 1: '#2ecc71', 6: '#3498db', 24: '#e74c3c' }
const HORIZON_LABELS = { 1: 't+1h', 6: 't+6h', 24: 't+24h' }

function horizonColor(h) { return HORIZON_COLORS[h] || '#999' }
function horizonLabel(h) { return HORIZON_LABELS[h] || `t+${h}h` }

// ======================================================================
// State
// ======================================================================
const loading = ref(false)
const error = ref(null)
const metadata = ref({})
const rawData = ref([])
const dailySummary = ref({})

// Filter controls
const selectedDate = ref('')
const selectedHorizons = ref([1, 6, 24])
const selectedExperiment = ref('stage5')
const selectedGranularity = ref('hour')
const selectedScenarioMode = ref('all')

// Metadata-driven options
const availableHorizons = ref([1, 6, 24])
const availableExperiments = ref(['stage5', 'e1'])
const dateMin = ref('')
const dateMax = ref('')

// ======================================================================
// Computed
// ======================================================================

/** Chart option built from raw data + current filters */
const chartOption = computed(() => {
  if (!rawData.value.length) return {}
  return buildInspectionChart(rawData.value, {
    horizons: selectedHorizons.value,
    experiment: selectedExperiment.value,
    scenarioMode: selectedScenarioMode.value,
  })
})

/** Main horizon: first selected, fallback to 6 */
const mainHorizon = computed(() => selectedHorizons.value[0] || 6)

/** Date range text for top bar */
const dateRangeText = computed(() => {
  if (!selectedDate.value) return ''
  const d = new Date(selectedDate.value)
  const start = new Date(d)
  start.setDate(start.getDate() - 1)
  const end = new Date(d)
  end.setDate(end.getDate() + 1)
  return `${fmtDate(start)} ~ ${fmtDate(end)} (3日窗口)`
})

/** Whether current date is at min boundary */
const isAtMinDate = computed(() => {
  if (!dateMin.value || !selectedDate.value) return false
  return selectedDate.value <= dateMin.value
})

/** Whether current date is at max boundary */
const isAtMaxDate = computed(() => {
  if (!dateMax.value || !selectedDate.value) return false
  return selectedDate.value >= dateMax.value
})

/** Compute per-horizon metrics (daytime only) */
function computeMetrics(data, horizon, experiment) {
  const filtered = data.filter(
    d => d.horizon_hours === horizon && d.experiment === experiment
  )
  const day = filtered.filter(d => {
    const elev = d.solar_elevation_deg
    return elev != null && elev > 5
  })
  if (!day.length) return { rmse: 0, mae: 0, bias: 0, nrmse: 0, count: 0 }

  const errors = day.map(d => d.error_kw ?? (d.prediction_kw - d.actual_kw))
  const actuals = day.map(d => d.actual_kw)
  const n = errors.length
  const sumSq = errors.reduce((s, e) => s + e * e, 0)
  const rmse = Math.sqrt(sumSq / n)
  const mae = errors.reduce((s, e) => s + Math.abs(e), 0) / n
  const bias = errors.reduce((s, e) => s + e, 0) / n
  const meanActual = actuals.reduce((s, a) => s + a, 0) / actuals.length
  const nrmse = meanActual > 0 ? rmse / meanActual : 0
  return { rmse, mae, bias, nrmse, count: n }
}

/** Compute persistence metrics for comparison */
function computePersistenceMetrics(data, horizon, experiment) {
  const filtered = data.filter(
    d =>
      d.horizon_hours === horizon &&
      d.experiment === experiment &&
      d.solar_elevation_deg != null &&
      d.solar_elevation_deg > 5
  )
  if (!filtered.length) return { rmse: 0 }

  const errors = filtered.map(
    d => (d.persistence_origin_kw ?? 0) - d.actual_kw
  )
  const n = errors.length
  const rmse = Math.sqrt(errors.reduce((s, e) => s + e * e, 0) / n)
  return { rmse }
}

/** Current horizon metrics */
const horizonMetrics = computed(() =>
  computeMetrics(rawData.value, mainHorizon.value, selectedExperiment.value)
)

/** Current persistence metrics */
const persistenceMetrics = computed(() =>
  computePersistenceMetrics(
    rawData.value,
    mainHorizon.value,
    selectedExperiment.value
  )
)

/** Daily energy from daily_summary */
const dailyEnergyMetrics = computed(() => {
  const summary = dailySummary.value
  let totalActual = 0
  let totalPred = 0
  const key = `${selectedExperiment.value}_t${mainHorizon.value}h`
  Object.values(summary).forEach(day => {
    totalActual += day.daily_actual_kwh || 0
    const pred = day[key]?.daily_pred_kwh
    if (pred != null) totalPred += pred
  })
  const errorPct =
    totalActual > 0 ? ((totalPred - totalActual) / totalActual) * 100 : 0
  return { actualKwh: totalActual, predKwh: totalPred, errorPct }
})

/** Improvement ratio vs persistence */
const improvementRatio = computed(() => {
  const pRmse = persistenceMetrics.value.rmse
  const hRmse = horizonMetrics.value.rmse
  if (pRmse > 0) return ((1 - hRmse / pRmse) * 100).toFixed(1)
  return '-'
})

/** Metric cards array for MetricGrid */
const metricCards = computed(() => {
  const h = horizonMetrics.value
  const d = dailyEnergyMetrics.value
  const p = persistenceMetrics.value
  const hl = horizonLabel(mainHorizon.value)

  return [
    {
      label: `${hl} MAE`,
      value: h.mae.toFixed(4),
      icon: 'TrendCharts',
      gradient: 'var(--gradient-cyan)',
    },
    {
      label: `${hl} RMSE`,
      value: h.rmse.toFixed(4),
      icon: 'TrendCharts',
      gradient: 'var(--gradient-orange)',
    },
    {
      label: `${hl} nRMSE`,
      value: h.nrmse.toFixed(4),
      icon: 'TrendCharts',
      gradient: 'var(--gradient-purple)',
    },
    {
      label: `${hl} Bias`,
      value: h.bias.toFixed(4),
      icon: 'TrendCharts',
      gradient: 'var(--gradient-red)',
    },
    {
      label: '日发电量 (Actual)',
      value: `${d.actualKwh.toFixed(2)} kWh`,
      icon: 'Sunny',
      gradient: 'var(--gradient-green)',
    },
    {
      label: '日发电量 (Predicted)',
      value: `${d.predKwh.toFixed(2)} kWh`,
      icon: 'Sunny',
      gradient: 'var(--gradient-cyan)',
    },
    {
      label: '电量偏差',
      value: `${d.errorPct.toFixed(2)}%`,
      icon: 'Sunny',
      gradient:
        d.errorPct > 0 ? 'var(--gradient-red)' : 'var(--gradient-green)',
    },
    {
      label: 'Persistence RMSE',
      value: p.rmse.toFixed(4),
      icon: 'DataLine',
      gradient: 'var(--gradient-blue)',
    },
    {
      label: '提升率 vs Persistence',
      value: improvementRatio.value,
      icon: 'DataLine',
      gradient:
        Number(improvementRatio.value) > 0
          ? 'var(--gradient-green)'
          : 'var(--gradient-red)',
    },
  ]
})

// ======================================================================
// Methods
// ======================================================================

/** Format a Date to YYYY-MM-DD string */
function fmtDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Navigate date by delta days */
function goDate(delta) {
  if (!selectedDate.value) return
  const d = new Date(selectedDate.value)
  d.setDate(d.getDate() + delta)
  selectedDate.value = fmtDate(d)
}

/** Disable dates outside the available range */
function disableDate(time) {
  if (!dateMin.value || !dateMax.value) return false
  const t = fmtDate(time)
  return t < dateMin.value || t > dateMax.value
}

/** Load metadata from API */
async function loadMetadata() {
  try {
    const meta = await fetchInspectionMetadata()
    metadata.value = meta

    // Populate available options from metadata
    if (meta.date_range) {
      dateMin.value = meta.date_range.min || meta.date_range.min_date || ''
      dateMax.value = meta.date_range.max || meta.date_range.max_date || ''
    }
    // Fallback if metadata has horizons/experiments at top level
    if (meta.horizons && Array.isArray(meta.horizons)) {
      availableHorizons.value = meta.horizons
    }
    if (meta.experiments && Array.isArray(meta.experiments)) {
      availableExperiments.value = meta.experiments
    }

    // Default selected date to max available date
    if (!selectedDate.value && dateMax.value) {
      selectedDate.value = dateMax.value
    } else if (!selectedDate.value) {
      selectedDate.value = fmtDate(new Date())
    }
  } catch (e) {
    console.error('Failed to load inspection metadata', e)
    // Set a fallback date so the UI still works
    if (!selectedDate.value) selectedDate.value = fmtDate(new Date())
  }
}

/** Fetch inspection data based on current filter state */
async function loadData() {
  if (!selectedDate.value) return

  loading.value = true
  error.value = null
  try {
    // Compute 3-day window centered on selected date
    const center = new Date(selectedDate.value)
    const start = new Date(center)
    start.setDate(start.getDate() - 1)
    const end = new Date(center)
    end.setDate(end.getDate() + 1)

    const params = {
      start: fmtDate(start),
      end: fmtDate(end),
      horizons: selectedHorizons.value.join(','),
      experiments: selectedExperiment.value,
    }

    // Pass granularity to API if it affects data aggregation
    if (selectedGranularity.value !== 'hour') {
      params.granularity = selectedGranularity.value
    }

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

// ======================================================================
// Watchers — re-fetch data when any filter changes
// ======================================================================
watch(
  [selectedDate, selectedHorizons, selectedExperiment, selectedGranularity],
  () => {
    loadData()
  }
)

// ======================================================================
// Lifecycle
// ======================================================================
onMounted(async () => {
  await loadMetadata()
  // Watcher on selectedDate will trigger loadData automatically
})
</script>

<style scoped>
/* ---- Layout ---- */
.inspection {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

/* ---- Top bar ---- */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
}
.top-bar-left {
  display: flex;
  align-items: baseline;
  gap: var(--space-md);
}
.top-bar-left h2 {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.25;
}
.model-version {
  color: var(--accent-cyan);
  font-family: var(--font-mono);
  font-size: 12px;
  background: rgba(0, 212, 255, 0.1);
  border: 1px solid rgba(0, 212, 255, 0.2);
  border-radius: var(--radius-full);
  padding: 2px 10px;
}
.range-hint {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

/* ---- Main row ---- */
.main-row {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  gap: var(--space-lg);
}

/* ---- Left panel ---- */
.left-panel {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: var(--space-md);
  height: fit-content;
  min-width: 0;
}
.control-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  padding: var(--space-sm) 0;
}
.ctrl-label {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.03em;
}
.ctrl-divider {
  margin: 2px 0;
  border-color: var(--border-glass);
}

/* Date row */
.date-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.date-nav-btn {
  flex: 0 0 auto;
  padding: 6px 8px;
  font-size: 14px;
  line-height: 1;
}
.date-picker {
  flex: 1;
  min-width: 0;
}
.date-picker :deep(.el-input__wrapper) {
  padding: 0 8px;
}
.date-picker :deep(.el-input__inner) {
  font-size: 12px;
  text-align: center;
}

/* Checkbox group */
.ctrl-checkbox-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.ctrl-checkbox-group .el-checkbox {
  margin-right: 0;
  height: auto;
}

/* Radio group */
.ctrl-radio-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.ctrl-radio-group .el-radio {
  margin-right: 0;
}

/* ---- Right panel ---- */
.right-panel {
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.inspection-chart {
  height: 520px;
  width: 100%;
}

/* ---- Bottom bar ---- */
.bottom-bar {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
.bottom-section-title {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
}
.section-hint {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
}

/* ---- Responsive ---- */
@media (max-width: 1199px) {
  .main-row {
    grid-template-columns: 1fr;
  }
  .left-panel {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: var(--space-sm);
  }
  .ctrl-divider {
    display: none;
  }
}

@media (max-width: 767px) {
  .top-bar {
    flex-direction: column;
    align-items: flex-start;
  }
  .inspection-chart {
    height: 400px;
  }
}
</style>
