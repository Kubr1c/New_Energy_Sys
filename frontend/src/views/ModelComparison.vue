<template>
  <div class="model-comparison">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载模型评估"
      message="正在读取表格模型、深度学习模型和测试集指标。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="模型评估加载失败"
      :message="error.message"
      retryable
      @retry="loadModels"
    />
    <PageState
      v-else-if="!allTestMetrics.length"
      type="empty"
      title="暂无模型指标"
      message="当前接口没有返回测试集模型指标，无法展示排行榜和图表。"
      retryable
      @retry="loadModels"
    />
    <template v-else>

      <PageSection title="预测模型测试结果">
        <template #actions>
          <span class="table-hint">同一预测时长内比较，误差越低越好。</span>
          <div class="leaderboard-filters">
            <el-select v-model="selectedTarget" size="small" style="width: 150px">
              <el-option
                v-for="option in targetOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-select v-model="selectedFeatureSet" size="small" style="width: 220px">
              <el-option
                v-for="option in featureSetOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </div>
        </template>
        <el-table
          :data="sortedTopMetrics"
          row-key="rankKey"
          style="width: 100%"
          stripe
          :row-class-name="leaderboardRowClass"
          :default-sort="{ prop: 'nrmse_capacity', order: 'ascending' }"
          max-height="420"
        >
          <el-table-column label="#" width="60">
            <template #default="{ $index }">{{ $index + 1 }}</template>
          </el-table-column>
          <el-table-column prop="model" label="模型" min-width="150" sortable>
            <template #default="{ row }">
              <span :style="{ color: modelColor(row.model) }">{{ modelLabel(row.model) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="feature_set" label="特征集" min-width="170">
            <template #default="{ row }"><span class="feat-tag">{{ featureSetLabel(row.feature_set) }}</span></template>
          </el-table-column>
          <el-table-column prop="target" label="预测目标/时长" width="130">
            <template #default="{ row }"><span class="feat-tag">{{ targetLabel(row.target) }}</span></template>
          </el-table-column>
          <el-table-column prop="nrmse_capacity" label="nRMSE" width="110" sortable>
            <template #default="{ row }">{{ fmtNum(row.nrmse_capacity) }}</template>
          </el-table-column>
          <el-table-column prop="mae_kw" label="MAE (kW)" width="120" sortable>
            <template #default="{ row }">{{ fmtNum(row.mae_kw) }}</template>
          </el-table-column>
          <el-table-column prop="rmse_kw" label="RMSE (kW)" width="120" sortable>
            <template #default="{ row }">{{ fmtNum(row.rmse_kw) }}</template>
          </el-table-column>
          <el-table-column prop="daytime_nrmse_capacity" label="日间 nRMSE" width="130" sortable>
            <template #default="{ row }">{{ fmtNum(row.daytime_nrmse_capacity) }}</template>
          </el-table-column>
        </el-table>
      </PageSection>

      <div class="chart-row">
        <ChartCard title="测试集误差对比">
          <p class="chart-note">用于比较不同预测模型在同一预测时长下的误差表现，数值越低表示误差越小。</p>
          <v-chart class="chart" :option="barChartOption" theme="dark-tech" autoresize />
        </ChartCard>
        <ChartCard title="多指标误差展示">
          <p class="chart-note">雷达图用于同时展示多项误差指标，越靠近中心表示整体误差越低。</p>
          <v-chart class="chart" :option="radarChartOption" theme="dark-tech" autoresize />
        </ChartCard>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, RadarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildModelBarChartOption, buildModelRadarChartOption, fmtNum, modelColor } from '../charts/modelCharts'
import { fetchModelComparison } from '../services/modelService'
import { normalizeApiError } from '../utils/api'
import { featureSetLabel, modelLabel, targetLabel } from '../utils/displayLabels'

use([CanvasRenderer, BarChart, RadarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent])

const PAGE_SIZE = 10

const tabularMetrics = ref([])
const dlMetrics = ref([])
const selectedTarget = ref('target_pv_power_t_plus_24h')
const selectedFeatureSet = ref('all')
const currentPage = ref(1)
const loading = ref(false)
const error = ref(null)

const allTestMetrics = computed(() => [...tabularMetrics.value, ...dlMetrics.value].filter(row => row.split === 'test'))
const targetOptions = computed(() => {
  const targets = [...new Set(allTestMetrics.value.map(row => row.target).filter(Boolean))]
    .sort((a, b) => targetLabel(a).localeCompare(targetLabel(b), 'zh-CN'))
  return targets.map(value => ({ label: targetLabel(value), value }))
})
const targetFilteredMetrics = computed(() => {
  if (!selectedTarget.value) return allTestMetrics.value
  return allTestMetrics.value.filter(row => row.target === selectedTarget.value)
})
const featureSetOptions = computed(() => {
  const sets = [...new Set(targetFilteredMetrics.value.map(row => row.feature_set).filter(Boolean))]
    .sort((a, b) => featureSetLabel(a).localeCompare(featureSetLabel(b), 'zh-CN'))
  return [
    { label: '全部特征集', value: 'all' },
    ...sets.map(value => ({ label: featureSetLabel(value), value })),
  ]
})
const filteredTestMetrics = computed(() => {
  if (selectedFeatureSet.value === 'all') return targetFilteredMetrics.value
  return targetFilteredMetrics.value.filter(row => row.feature_set === selectedFeatureSet.value)
})
const sortedMetrics = computed(() => [...filteredTestMetrics.value]
  .sort((a, b) => Number(a.nrmse_capacity || Infinity) - Number(b.nrmse_capacity || Infinity))
  .map((row, index) => ({ ...row, rankKey: `${row.model}-${row.feature_set}-${row.target}-${row.window_size}-${index}` })))
const sortedTopMetrics = computed(() => sortedMetrics.value.slice((currentPage.value - 1) * PAGE_SIZE, currentPage.value * PAGE_SIZE))
const bestMetric = computed(() => sortedMetrics.value[0] || {})
const baselineMetric = computed(() => sortedMetrics.value.find(row => /persistence|baseline|linear/i.test(String(row.model))) || sortedMetrics.value[sortedMetrics.value.length - 1] || {})
const modelInsight = computed(() => {
  const bestNrmse = Number(bestMetric.value.nrmse_capacity)
  const baseNrmse = Number(baselineMetric.value.nrmse_capacity)
  const improvement = Number.isFinite(bestNrmse) && Number.isFinite(baseNrmse) && baseNrmse > 0
    ? `${(((baseNrmse - bestNrmse) / baseNrmse) * 100).toFixed(1)}%`
    : '无法计算'
  return {
    title: bestMetric.value.model
      ? `测试集最优模型为 ${modelLabel(bestMetric.value.model)}，nRMSE 为 ${fmtNum(bestMetric.value.nrmse_capacity)}。`
      : '当前筛选条件下没有可用模型指标。',
    tone: bestMetric.value.model ? 'positive' : 'warning',
    items: [
      `最优特征集：${featureSetLabel(bestMetric.value.feature_set)}。`,
      `预测时长：${targetLabel(selectedTarget.value)}。`,
      `相对 ${modelLabel(baselineMetric.value.model)} 的 nRMSE 改善：${improvement}。`,
      `当前筛选范围包含 ${sortedMetrics.value.length} 条测试集记录；默认页显示前 ${PAGE_SIZE} 条。`,
    ],
  }
})

const barChartOption = computed(() => buildModelBarChartOption(sortedTopMetrics.value))
const radarChartOption = computed(() => buildModelRadarChartOption(sortedTopMetrics.value))

watch([selectedTarget, selectedFeatureSet], () => {
  currentPage.value = 1
})

watch(targetOptions, (options) => {
  if (!options.length) return
  if (!options.some(option => option.value === selectedTarget.value)) {
    selectedTarget.value = options.some(option => option.value === 'target_pv_power_t_plus_24h')
      ? 'target_pv_power_t_plus_24h'
      : options[0].value
  }
})

function leaderboardRowClass({ rowIndex }) {
  return rowIndex < 3 ? 'top-model-row' : ''
}

async function loadModels() {
  loading.value = true
  error.value = null
  try {
    const data = await fetchModelComparison()
    tabularMetrics.value = data.tabularMetrics
    dlMetrics.value = data.deepLearningMetrics
    currentPage.value = 1
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadModels)
</script>

<style scoped>
.model-comparison { display: flex; flex-direction: column; gap: var(--space-lg); }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }
.chart { height: 360px; width: 100%; }
.table-hint,
.chart-note { color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.chart-note { margin-bottom: var(--space-md); }
.leaderboard-filters { display: inline-flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.feat-tag {
  background: rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-full);
  color: var(--text-secondary);
  font-size: 11px;
  padding: 2px 8px;
}
:deep(.top-model-row td) { background: rgba(0, 212, 255, 0.055) !important; }

@media (max-width: 1199px) {
  .chart-row { grid-template-columns: 1fr; }
}

@media (max-width: 767px) {
  .chart { height: 320px; }
}
</style>
