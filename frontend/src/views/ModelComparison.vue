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
      <InsightSummary :title="modelInsight.title" :items="modelInsight.items" :tone="modelInsight.tone" />

      <PageSection title="模型排行榜">
        <template #actions>
          <span class="table-hint">默认展示全部记录，图表聚焦前 12 / 前 5</span>
          <el-select v-model="selectedFeatureSet" size="small" style="width: 200px">
            <el-option label="全部特征集" value="all" />
            <el-option label="仅历史特征" value="history_only" />
            <el-option label="完整特征" value="full_features_without_target_plus" />
            <el-option label="天气 + 历史" value="weather_history_target_aligned" />
          </el-select>
        </template>
        <el-table
          :data="rankedMetrics"
          row-key="rankKey"
          style="width: 100%"
          stripe
          :row-class-name="leaderboardRowClass"
          :default-sort="{ prop: 'nrmse_capacity', order: 'ascending' }"
          max-height="360"
        >
          <el-table-column label="#" width="60">
            <template #default="{ $index }">{{ $index + 1 }}</template>
          </el-table-column>
          <el-table-column prop="model" label="模型" min-width="150" sortable>
            <template #default="{ row }"><span :style="{ color: modelColor(row.model) }">{{ row.model }}</span></template>
          </el-table-column>
          <el-table-column prop="feature_set" label="特征集" min-width="130">
            <template #default="{ row }"><span class="feat-tag">{{ shortFeature(row.feature_set) }}</span></template>
          </el-table-column>
          <el-table-column prop="nrmse_capacity" label="nRMSE" width="110" sortable>
            <template #default="{ row }">{{ fmtNum(row.nrmse_capacity) }}</template>
          </el-table-column>
          <el-table-column prop="mae_kw" label="MAE (kW)" width="110" sortable>
            <template #default="{ row }">{{ fmtNum(row.mae_kw) }}</template>
          </el-table-column>
          <el-table-column prop="rmse_kw" label="RMSE (kW)" width="110" sortable>
            <template #default="{ row }">{{ fmtNum(row.rmse_kw) }}</template>
          </el-table-column>
          <el-table-column prop="daytime_nrmse_capacity" label="Day nRMSE" width="120" sortable>
            <template #default="{ row }">{{ fmtNum(row.daytime_nrmse_capacity) }}</template>
          </el-table-column>
        </el-table>
      </PageSection>

      <div class="chart-row">
        <ChartCard title="模型 nRMSE 对比">
          <v-chart class="chart" :option="barChartOption" theme="dark-tech" autoresize />
        </ChartCard>
        <ChartCard title="多指标雷达图">
          <v-chart class="chart" :option="radarChartOption" theme="dark-tech" autoresize />
        </ChartCard>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, RadarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import InsightSummary from '../components/InsightSummary.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildModelBarChartOption, buildModelRadarChartOption, fmtNum, modelColor, shortFeature } from '../charts/modelCharts'
import { fetchModelComparison } from '../services/modelService'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, BarChart, RadarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent])

const tabularMetrics = ref([])
const dlMetrics = ref([])
const selectedFeatureSet = ref('all')
const loading = ref(false)
const error = ref(null)

const allTestMetrics = computed(() => [...tabularMetrics.value, ...dlMetrics.value].filter(row => row.split === 'test'))
const filteredTestMetrics = computed(() => {
  if (selectedFeatureSet.value === 'all') return allTestMetrics.value
  return allTestMetrics.value.filter(row => row.feature_set === selectedFeatureSet.value)
})
const rankedMetrics = computed(() => [...filteredTestMetrics.value]
  .sort((a, b) => Number(a.nrmse_capacity || Infinity) - Number(b.nrmse_capacity || Infinity))
  .map((row, index) => ({ ...row, rankKey: `${row.model}-${row.feature_set}-${index}` })))
const bestMetric = computed(() => rankedMetrics.value[0] || {})
const baselineMetric = computed(() => rankedMetrics.value.find(row => /linear|baseline/i.test(String(row.model))) || rankedMetrics.value[rankedMetrics.value.length - 1] || {})
const modelInsight = computed(() => {
  const bestNrmse = Number(bestMetric.value.nrmse_capacity)
  const baseNrmse = Number(baselineMetric.value.nrmse_capacity)
  const improvement = Number.isFinite(bestNrmse) && Number.isFinite(baseNrmse) && baseNrmse > 0
    ? `${(((baseNrmse - bestNrmse) / baseNrmse) * 100).toFixed(1)}%`
    : '无法计算'
  return {
    title: bestMetric.value.model
      ? `${bestMetric.value.model} 当前排名第一，测试集 nRMSE 为 ${fmtNum(bestMetric.value.nrmse_capacity)}。`
      : '当前筛选条件下没有可用模型指标。',
    tone: bestMetric.value.model ? 'positive' : 'warning',
    items: [
      `推荐特征集：${shortFeature(bestMetric.value.feature_set) || '数据缺失'}。`,
      `相对参考模型 ${baselineMetric.value.model || '数据缺失'} 的 nRMSE 改善：${improvement}。`,
      `当前筛选范围包含 ${rankedMetrics.value.length} 条测试集记录。`,
    ],
  }
})

const barChartOption = computed(() => buildModelBarChartOption(rankedMetrics.value))
const radarChartOption = computed(() => buildModelRadarChartOption(rankedMetrics.value))

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
.table-hint { color: var(--text-secondary); font-size: 12px; }
.feat-tag {
  background: rgba(255, 255, 255, 0.07);
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
