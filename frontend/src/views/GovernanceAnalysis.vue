<template>
  <div class="governance">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载策略治理"
      message="正在读取储能配置敏感性指标和 Pareto 标记。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="策略治理加载失败"
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
      <!-- Pareto Scatter -->
      <div class="glass-card pareto-card animate-fade-in-up">
        <h3>储能配置 Pareto 分析 — Sensitivity Analysis (Stage 15)</h3>
        <v-chart class="chart-lg" :option="paretoOption" theme="dark-tech" autoresize />
      </div>
    </div>

    <div class="bottom-section">
      <!-- Heatmap -->
      <div class="glass-card heat-card animate-fade-in-up animate-delay-2">
        <h3>容量×功率 增量收益热力图 — Revenue Heatmap</h3>
        <v-chart class="chart-md" :option="heatmapOption" theme="dark-tech" autoresize />
      </div>

      <!-- Config Table -->
      <div class="glass-card table-card animate-fade-in-up animate-delay-3">
        <h3>配置详情 — Configuration Details</h3>
        <el-table :data="sensitivity" style="width:100%" stripe max-height="350" size="small">
          <el-table-column prop="config_id" label="Config" width="180" />
          <el-table-column prop="capacity_kwh" label="Cap (kWh)" width="85" sortable>
            <template #default="{ row }">{{ Number(row.capacity_kwh).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="max_charge_kw" label="Power (kW)" width="85" sortable>
            <template #default="{ row }">{{ Number(row.max_charge_kw).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="incremental_revenue_eur" label="Incr Rev (€)" width="95" sortable>
            <template #default="{ row }"><span :class="Number(row.incremental_revenue_eur) >= 0 ? 'pos' : 'neg'">{{ Number(row.incremental_revenue_eur).toFixed(3) }}</span></template>
          </el-table-column>
          <el-table-column prop="cycle_equivalent_count" label="Cycles" width="75" sortable>
            <template #default="{ row }">{{ Number(row.cycle_equivalent_count).toFixed(0) }}</template>
          </el-table-column>
          <el-table-column prop="pareto_front" label="Pareto" width="70">
            <template #default="{ row }"><span class="pareto-tag" :class="{ active: row.pareto_front }">{{ row.pareto_front ? '✓' : '—' }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { ScatterChart, HeatmapChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent, VisualMapComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import api from '../utils/api'
import PageState from '../components/PageState.vue'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, ScatterChart, HeatmapChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, VisualMapComponent])

const sensitivity = ref([])
const loading = ref(false)
const error = ref(null)

const paretoOption = computed(() => {
  if (!sensitivity.value.length) return {}
  const pareto = sensitivity.value.filter(r => r.pareto_front === true || r.pareto_front === 'True')
  const nonPareto = sensitivity.value.filter(r => r.pareto_front !== true && r.pareto_front !== 'True')
  return {
    tooltip: { trigger: 'item', formatter: p => `${p.data[3]}<br/>Revenue: €${Number(p.data[0]).toFixed(3)}<br/>Cycles: ${Number(p.data[1]).toFixed(0)}<br/>Shortfall: ${Number(p.data[2]).toFixed(1)} kWh` },
    grid: { left: 60, right: 30, top: 30, bottom: 50 },
    xAxis: { type: 'value', name: 'Incremental Revenue (€)', nameLocation: 'center', nameGap: 30 },
    yAxis: { type: 'value', name: 'Cycle Count' },
    series: [
      { name: 'Pareto Front', type: 'scatter', data: pareto.map(r => [Number(r.incremental_revenue_eur), Number(r.cycle_equivalent_count), Number(r.total_shortfall_kwh), r.config_id]),
        symbolSize: 14, itemStyle: { color: '#00f5a0', shadowBlur: 10, shadowColor: 'rgba(0,245,160,0.4)' } },
      { name: 'Non-Pareto', type: 'scatter', data: nonPareto.map(r => [Number(r.incremental_revenue_eur), Number(r.cycle_equivalent_count), Number(r.total_shortfall_kwh), r.config_id]),
        symbolSize: 8, itemStyle: { color: 'rgba(255,255,255,0.25)' } },
    ],
  }
})

const heatmapOption = computed(() => {
  if (!sensitivity.value.length) return {}
  const capSet = [...new Set(sensitivity.value.map(r => Number(r.capacity_multiplier)))].sort()
  const powSet = [...new Set(sensitivity.value.map(r => Number(r.power_multiplier)))].sort()
  // Use average incremental revenue across objective presets
  const dataMap = {}
  sensitivity.value.forEach(r => {
    const key = `${r.capacity_multiplier}_${r.power_multiplier}`
    if (!dataMap[key]) dataMap[key] = []
    dataMap[key].push(Number(r.incremental_revenue_eur))
  })
  const heatData = []
  capSet.forEach((c, ci) => {
    powSet.forEach((p, pi) => {
      const vals = dataMap[`${c}_${p}`] || [0]
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length
      heatData.push([ci, pi, Number(avg.toFixed(4))])
    })
  })

  return {
    tooltip: { formatter: p => `Cap ×${capSet[p.data[0]]}, Pow ×${powSet[p.data[1]]}<br/>Avg Incr. Revenue: €${p.data[2]}` },
    grid: { left: 80, right: 80, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: capSet.map(c => `×${c}`), name: 'Capacity Multiplier' },
    yAxis: { type: 'category', data: powSet.map(p => `×${p}`), name: 'Power Multiplier' },
    visualMap: { min: -0.2, max: 2, calculable: true, orient: 'vertical', right: 10, top: 'center',
      inRange: { color: ['#1a237e', '#0d47a1', '#00838f', '#00c853', '#ffd600'] },
      textStyle: { color: 'rgba(255,255,255,0.6)' } },
    series: [{ type: 'heatmap', data: heatData, label: { show: true, color: '#fff', fontSize: 11, formatter: p => `€${p.data[2]}` },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,212,255,0.5)' } } }],
  }
})

async function loadSensitivity() {
  loading.value = true
  error.value = null
  try {
    const res = await api.get('/api/sensitivity/metrics')
    sensitivity.value = res.data
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadSensitivity)
</script>

<style scoped>
.governance { display: flex; flex-direction: column; gap: var(--space-xl); }
.top-section { display: grid; grid-template-columns: 1fr; gap: var(--space-lg); }
.bottom-section { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }

.pareto-card, .heat-card, .table-card { padding: var(--space-lg); }
.pareto-card h3, .heat-card h3, .table-card h3 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-md); }

.chart-lg { height: 400px; width: 100%; }
.chart-md { height: 320px; width: 100%; }

.pos { color: var(--accent-green); }
.neg { color: var(--accent-red); }
.pareto-tag { font-size: 13px; color: var(--text-tertiary); }
.pareto-tag.active { color: var(--accent-green); font-weight: 700; }

@media (max-width: 1199px) {
  .bottom-section { grid-template-columns: 1fr; }
  .pareto-card, .heat-card, .table-card { min-width: 0; }
}

@media (max-width: 767px) {
  .governance { gap: var(--space-lg); }
  .chart-lg { height: 340px; }
  .chart-md { height: 300px; }
}
</style>
