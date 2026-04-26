<template>
  <div class="model-comparison">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载模型对比"
      message="正在读取表格模型、深度学习模型和测试集指标。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="模型对比加载失败"
      :message="error.message"
      retryable
      @retry="loadModels"
    />
    <PageState
      v-else-if="!allTestMetrics.length"
      type="empty"
      title="暂无模型指标"
      message="当前接口没有返回测试集模型指标，无法绘制排行榜和雷达图。"
      retryable
      @retry="loadModels"
    />
    <template v-else>
    <div class="top-row">
      <!-- Model Ranking Table -->
      <div class="glass-card ranking-card animate-fade-in-up">
        <div class="section-header">
          <h3>📊 模型排行榜 Model Leaderboard (Test Set)</h3>
          <el-select v-model="selectedFeatureSet" size="small" style="width:200px">
            <el-option label="All Feature Sets" value="all" />
            <el-option label="History Only" value="history_only" />
            <el-option label="Full Features" value="full_features_without_target_plus" />
            <el-option label="Weather+History" value="weather_history_target_aligned" />
          </el-select>
        </div>
        <el-table :data="filteredTestMetrics" style="width:100%" stripe :default-sort="{ prop: 'nrmse_capacity', order: 'ascending' }" max-height="420">
          <el-table-column prop="model" label="Model" width="140" sortable>
            <template #default="{ row }"><span :style="{ color: modelColor(row.model) }">{{ row.model }}</span></template>
          </el-table-column>
          <el-table-column prop="feature_set" label="Features" width="120">
            <template #default="{ row }"><span class="feat-tag">{{ shortFeature(row.feature_set) }}</span></template>
          </el-table-column>
          <el-table-column prop="nrmse_capacity" label="nRMSE" width="100" sortable>
            <template #default="{ row }">{{ fmtNum(row.nrmse_capacity) }}</template>
          </el-table-column>
          <el-table-column prop="mae_kw" label="MAE (kW)" width="100" sortable>
            <template #default="{ row }">{{ fmtNum(row.mae_kw) }}</template>
          </el-table-column>
          <el-table-column prop="rmse_kw" label="RMSE (kW)" width="100" sortable>
            <template #default="{ row }">{{ fmtNum(row.rmse_kw) }}</template>
          </el-table-column>
          <el-table-column prop="daytime_nrmse_capacity" label="Day nRMSE" width="100" sortable>
            <template #default="{ row }">{{ fmtNum(row.daytime_nrmse_capacity) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="chart-row">
      <!-- nRMSE Bar Chart -->
      <div class="glass-card chart-card animate-fade-in-up animate-delay-2">
        <h3>模型 nRMSE 对比 — Model nRMSE Comparison</h3>
        <v-chart class="chart" :option="barChartOption" theme="dark-tech" autoresize />
      </div>

      <!-- Radar Chart -->
      <div class="glass-card chart-card animate-fade-in-up animate-delay-3">
        <h3>多维指标雷达图 — Radar</h3>
        <v-chart class="chart" :option="radarChartOption" theme="dark-tech" autoresize />
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, RadarChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import api from '../utils/api'
import PageState from '../components/PageState.vue'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, BarChart, RadarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent])

const tabularMetrics = ref([])
const dlMetrics = ref([])
const selectedFeatureSet = ref('all')
const loading = ref(false)
const error = ref(null)

const colorMap = {
  lightgbm_tuned: '#00d4ff', xgboost: '#00f5a0', catboost: '#ffa726',
  extra_trees: '#b388ff', random_forest: '#64b5f6', ridge: '#ff5252',
  elastic_net: '#ffee58', persistence: '#666', cnn_lstm: '#4dd0e1', attention_lstm: '#e040fb',
}
function modelColor(name) { return colorMap[name] || '#00d4ff' }
function fmtNum(v) { return v != null ? Number(v).toFixed(4) : '—' }
function shortFeature(f) {
  if (f === 'history_only') return 'hist'
  if (f === 'full_features_without_target_plus') return 'full'
  if (f === 'weather_history_target_aligned') return 'weather'
  if (f === 'persistence_baseline') return 'base'
  return f
}

const allTestMetrics = computed(() => {
  const combined = [...tabularMetrics.value, ...dlMetrics.value]
  return combined.filter(r => r.split === 'test')
})

const filteredTestMetrics = computed(() => {
  if (selectedFeatureSet.value === 'all') return allTestMetrics.value
  return allTestMetrics.value.filter(r => r.feature_set === selectedFeatureSet.value)
})

const barChartOption = computed(() => {
  const data = [...allTestMetrics.value].sort((a, b) => (a.nrmse_capacity || 0) - (b.nrmse_capacity || 0))
  const labels = data.map(r => `${r.model}\n(${shortFeature(r.feature_set)})`)
  const values = data.map(r => Number(r.nrmse_capacity || 0).toFixed(4))
  const colors = data.map(r => modelColor(r.model))

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 160, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', name: 'nRMSE' },
    yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 10 } },
    series: [{ type: 'bar', data: values.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })), barMaxWidth: 20 }],
  }
})

const radarChartOption = computed(() => {
  const metrics = allTestMetrics.value.filter(r => r.feature_set === 'history_only' || r.feature_set === 'weather_history_target_aligned')
  if (!metrics.length) return {}
  const maxVals = { nrmse_capacity: 0.25, mae_kw: 0.15, rmse_kw: 0.25, daytime_nrmse_capacity: 0.25, daytime_mape: 1.5 }
  const indicator = [
    { name: 'nRMSE', max: maxVals.nrmse_capacity },
    { name: 'MAE', max: maxVals.mae_kw },
    { name: 'RMSE', max: maxVals.rmse_kw },
    { name: 'Day nRMSE', max: maxVals.daytime_nrmse_capacity },
    { name: 'Day MAPE', max: maxVals.daytime_mape },
  ]
  // Pick top 5 unique models
  const seen = new Set()
  const unique = metrics.filter(r => { if (seen.has(r.model)) return false; seen.add(r.model); return true }).slice(0, 5)
  const seriesData = unique.map(r => ({
    name: r.model,
    value: [r.nrmse_capacity, r.mae_kw, r.rmse_kw, r.daytime_nrmse_capacity, r.daytime_mape].map(v => Number(v || 0)),
    lineStyle: { color: modelColor(r.model) },
    itemStyle: { color: modelColor(r.model) },
    areaStyle: { color: modelColor(r.model), opacity: 0.1 },
  }))

  return {
    legend: { data: unique.map(r => r.model), top: 0, textStyle: { fontSize: 11 } },
    radar: { indicator, center: ['50%', '55%'], radius: '60%' },
    series: [{ type: 'radar', data: seriesData }],
  }
})

async function loadModels() {
  loading.value = true
  error.value = null
  try {
    const [tabRes, dlRes] = await Promise.all([
      api.get('/api/models/tabular'),
      api.get('/api/models/deep-learning'),
    ])
    tabularMetrics.value = tabRes.data
    dlMetrics.value = dlRes.data
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadModels)
</script>

<style scoped>
.model-comparison { display: flex; flex-direction: column; gap: var(--space-xl); }

.top-row { display: grid; grid-template-columns: 1fr; gap: var(--space-lg); }
.ranking-card { padding: var(--space-lg); }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-md); }
.section-header h3 { font-size: 15px; font-weight: 600; }

.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }
.chart-card { padding: var(--space-lg); }
.chart-card h3 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-md); }
.chart { height: 380px; width: 100%; }

.feat-tag {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(255,255,255,0.06);
  border-radius: var(--radius-full);
  color: var(--text-secondary);
}

@media (max-width: 1199px) {
  .chart-row { grid-template-columns: 1fr; }
  .ranking-card, .chart-card { min-width: 0; }
}

@media (max-width: 767px) {
  .model-comparison { gap: var(--space-lg); }
  .section-header { align-items: flex-start; flex-direction: column; gap: 12px; }
  .chart { height: 320px; }
}
</style>
