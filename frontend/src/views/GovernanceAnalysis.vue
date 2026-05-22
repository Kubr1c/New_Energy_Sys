<template>
  <div class="governance">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载配置敏感性"
      message="正在读取容量、功率变化后的收益结果。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="配置敏感性加载失败"
      :message="error.message"
      retryable
      @retry="loadSensitivity"
    />
    <PageState
      v-else-if="!sensitivity.length"
      type="empty"
      title="暂无敏感性指标"
      message="当前接口没有返回储能配置敏感性结果。"
      retryable
      @retry="loadSensitivity"
    />
    <template v-else>

      <div class="top-section">
        <ChartCard title="容量功率对收益的影响">
          <p class="chart-note">横轴为收益，纵轴为循环次数；越靠右表示收益越高。</p>
          <v-chart class="chart-lg" :option="paretoOption" theme="dark-tech" autoresize />
        </ChartCard>
      </div>

      <div class="bottom-section">
        <ChartCard title="容量和功率组合收益图">
          <p class="chart-note">颜色越深表示当前结果中的收益越高。</p>
          <v-chart class="chart-md" :option="heatmapOption" theme="dark-tech" autoresize />
        </ChartCard>

        <PageSection title="配置组合明细">
          <el-table :data="sensitivity" style="width:100%" stripe max-height="350" size="small">
            <el-table-column prop="config_id" label="配置组合" min-width="220">
              <template #default="{ row }">
                <span>{{ formatConfigName(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="capacity_multiplier" label="容量倍率" width="105" sortable>
              <template #default="{ row }">{{ formatMultiplier(row.capacity_multiplier ?? parseConfigId(row.config_id).capMultiplier) }}</template>
            </el-table-column>
            <el-table-column prop="power_multiplier" label="功率倍率" width="105" sortable>
              <template #default="{ row }">{{ formatMultiplier(row.power_multiplier ?? parseConfigId(row.config_id).powMultiplier) }}</template>
            </el-table-column>
            <el-table-column prop="incremental_revenue_eur" label="相比无储能收益（元）" width="170" sortable>
              <template #default="{ row }">
                <span :class="Number(row.incremental_revenue_eur) >= 0 ? 'pos' : 'neg'">
                  {{ formatYuanFromEur(row.incremental_revenue_eur, 2) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="cycle_equivalent_count" label="循环" width="85" sortable>
              <template #default="{ row }">{{ Number(row.cycle_equivalent_count).toFixed(0) }}</template>
            </el-table-column>
            <el-table-column prop="pareto_front" label="较优组合" width="100">
              <template #default="{ row }">
                <span class="pareto-tag" :class="{ active: isTrue(row.pareto_front) }">{{ isTrue(row.pareto_front) ? '是' : '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </PageSection>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { HeatmapChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildParetoOption, buildRevenueHeatmapOption } from '../charts/governanceCharts'
import { fetchSensitivityMetrics } from '../services/governanceService'
import { normalizeApiError } from '../utils/api'
import { formatYuanFromEur } from '../utils/currency'

use([CanvasRenderer, ScatterChart, HeatmapChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, VisualMapComponent])

const sensitivity = ref([])
const loading = ref(false)
const error = ref(null)

const paretoOption = computed(() => buildParetoOption(sensitivity.value))
const heatmapOption = computed(() => buildRevenueHeatmapOption(sensitivity.value))
const paretoRows = computed(() => sensitivity.value.filter(row => isTrue(row.pareto_front)))
const bestConfig = computed(() => {
  const candidates = paretoRows.value.length ? paretoRows.value : sensitivity.value
  return [...candidates].sort((a, b) => Number(b.incremental_revenue_eur || -Infinity) - Number(a.incremental_revenue_eur || -Infinity))[0] || {}
})

function isTrue(value) {
  return value === true || value === 'True' || value === 'true' || value === 1 || value === '1'
}

function formatNumber(value, digits = 2) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : '数据缺失'
}

function parseConfigId(configId) {
  const raw = String(configId || '')
  const capMatch = raw.match(/cap(\d+p?\d*)/i)
  const powMatch = raw.match(/pow(\d+p?\d*)/i)
  const objMatch = raw.match(/obj(\d+)/i)
  return {
    capMultiplier: capMatch ? parseFloat(capMatch[1].replace('p', '.')) : null,
    powMultiplier: powMatch ? parseFloat(powMatch[1].replace('p', '.')) : null,
    objPreset: objMatch ? parseInt(objMatch[1], 10) : null,
  }
}

function formatMultiplier(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${n.toFixed(1)} 倍` : '-'
}

function formatConfigName(row) {
  const cap = row.capacity_multiplier != null ? row.capacity_multiplier : parseConfigId(row.config_id).capMultiplier
  const pow = row.power_multiplier != null ? row.power_multiplier : parseConfigId(row.config_id).powMultiplier
  const obj = row.objective_preset != null ? row.objective_preset : parseConfigId(row.config_id).objPreset
  const parts = []
  if (cap != null) parts.push(`容量 ${formatMultiplier(cap)}`)
  if (pow != null) parts.push(`功率 ${formatMultiplier(pow)}`)
  if (obj != null) parts.push(`目标组合 ${String(obj).replace(/^objective_?/i, '')}`)
  return parts.length ? parts.join(' / ') : '配置组合'
}

async function loadSensitivity() {
  loading.value = true
  error.value = null
  try {
    sensitivity.value = await fetchSensitivityMetrics()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadSensitivity)
</script>

<style scoped>
.governance { display: flex; flex-direction: column; gap: var(--space-lg); }
.top-section { display: grid; grid-template-columns: 1fr; gap: var(--space-lg); }
.bottom-section { display: grid; grid-template-columns: minmax(0, 1fr) minmax(420px, 0.95fr); gap: var(--space-lg); }
.chart-note { color: var(--text-secondary); font-size: 12px; margin: -4px 0 10px; }
.chart-lg { height: 380px; width: 100%; }
.chart-md { height: 320px; width: 100%; }
.pos { color: var(--accent-green); }
.neg { color: var(--accent-red); }
.pareto-tag { color: var(--text-tertiary); font-size: 12px; }
.pareto-tag.active { color: var(--accent-green); font-weight: 700; }

@media (max-width: 1199px) {
  .bottom-section { grid-template-columns: 1fr; }
}

@media (max-width: 767px) {
  .chart-lg { height: 340px; }
  .chart-md { height: 300px; }
}
</style>
