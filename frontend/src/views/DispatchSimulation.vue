<template>
  <div class="dispatch">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载调度仿真"
      message="正在读取策略治理评分、Rawhide 实站映射和退化回放指标。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="调度仿真加载失败"
      :message="error.message"
      retryable
      @retry="loadScorecard"
    />
    <PageState
      v-else-if="!scorecard.length && !rawhideAvailable"
      type="empty"
      title="暂无调度策略数据"
      message="当前接口没有返回可展示的策略评分或 Rawhide 仿真结果。"
      retryable
      @retry="loadScorecard"
    />
    <template v-else>
      <InsightSummary :title="dispatchInsight.title" :items="dispatchInsight.items" :tone="dispatchInsight.tone" />

      <el-tabs v-model="activeTab" class="dispatch-tabs">
        <el-tab-pane label="实站收益" name="revenue">
          <section v-if="rawhideAvailable" class="tab-stack">
            <div class="rawhide-hero glass-panel">
              <div>
                <span class="kicker">S18 实站参考仿真</span>
                <h2>{{ referenceSite.name || 'Rawhide Prairie Solar' }}</h2>
                <p>{{ referenceSite.location || 'Larimer County, Colorado' }}</p>
              </div>
              <div class="rawhide-facts">
                <div><span>PV</span><strong>{{ formatMw(referenceSite.pv_capacity_kw_ac) }}</strong></div>
                <div><span>BESS</span><strong>{{ formatKw(referenceSite.battery_power_kw) }} / {{ formatKwh(referenceSite.battery_energy_kwh) }}</strong></div>
                <div><span>投运年份</span><strong>{{ referenceSite.commercial_operation_year || '2021' }}</strong></div>
                <div><span>缩放系数</span><strong>{{ formatNumber(rawhideScaling.source_scale_factor, 3) }}</strong></div>
              </div>
            </div>

            <div class="boundary-panel glass-panel">
              <strong>边界说明</strong>
              <p>Rawhide 使用公开容量参数；发电曲线由 PVDAQ System 10 等比例放大；电价仍为 OPSD 映射价格。因此该页面用于策略相对价值演示，不代表 Rawhide 实测结算收益。</p>
            </div>

            <div class="rawhide-metric-grid">
              <div v-for="item in rawhideKpis.slice(0, 3)" :key="item.label" class="rawhide-metric glass-card">
                <span>{{ item.label }}</span>
                <strong class="display-number" :class="item.className">{{ item.value }}</strong>
                <small>{{ item.hint }}</small>
              </div>
            </div>

            <ChartCard title="Rawhide 策略增量收益">
              <p class="chart-note">红色为负收益或失败基线，橙色为离线上界，青色为滚动策略结果。</p>
              <v-chart class="chart" :option="rawhideRevenueOption" theme="dark-tech" autoresize />
            </ChartCard>
          </section>
          <PageState v-else type="empty" title="暂无 Rawhide 仿真数据" message="未读取到 S18 Rawhide 报告或指标文件。" />
        </el-tab-pane>

        <el-tab-pane label="退化回放" name="degradation" lazy>
          <section class="tab-stack">
            <div class="rawhide-metric-grid">
              <div v-for="item in rawhideKpis.slice(3, 5)" :key="item.label" class="rawhide-metric glass-card">
                <span>{{ item.label }}</span>
                <strong class="display-number" :class="item.className">{{ item.value }}</strong>
                <small>{{ item.hint }}</small>
              </div>
            </div>
            <ChartCard title="退化后净收益">
              <div class="degradation-panel">
                <div class="degradation-main">
                  <span>Rainflow 净增量收益</span>
                  <strong class="display-number" :class="Number(degradationRecommended.net_incremental_revenue_eur || 0) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(degradationRecommended.net_incremental_revenue_eur) }}</strong>
                  <p>净收益已扣除循环退化成本。若为负值，表示储能策略收益不足以覆盖退化损失。</p>
                </div>
                <div class="degradation-list">
                  <div><span>退化成本</span><strong>{{ formatCurrency(degradationRecommended.degradation_cost_eur) }}</strong></div>
                  <div><span>SOH</span><strong>{{ formatPercent(degradationRecommended.soh_start) }} -> {{ formatPercent(degradationRecommended.soh_end) }}</strong></div>
                  <div><span>等效满循环</span><strong>{{ formatNumber(degradationRecommended.equivalent_full_cycles, 1) }}</strong></div>
                  <div><span>容量衰减</span><strong>{{ formatNumber(degradationRecommended.capacity_fade_percent, 2) }}%</strong></div>
                </div>
              </div>
            </ChartCard>
          </section>
        </el-tab-pane>

        <el-tab-pane label="配置敏感性" name="pareto" lazy>
          <section class="tab-stack">
            <ChartCard title="Rawhide 配置敏感性 Pareto">
              <div class="pareto-summary">
                <span>推荐配置</span>
                <strong>{{ bestPareto.config_id || 'cap1p5_pow1p5_obj1' }}</strong>
                <p>{{ formatKwh(bestPareto.capacity_kwh) }} 容量，{{ formatKw(bestPareto.max_discharge_kw) }} 充放电功率，增量收益 {{ formatCurrency(bestPareto.incremental_revenue_eur) }}。</p>
              </div>
              <el-table class="pareto-table" :data="paretoRows" size="small">
                <el-table-column prop="config_id" label="配置" min-width="170" />
                <el-table-column label="容量" min-width="90"><template #default="{ row }">{{ formatKwh(row.capacity_kwh) }}</template></el-table-column>
                <el-table-column label="功率" min-width="90"><template #default="{ row }">{{ formatKw(row.max_discharge_kw) }}</template></el-table-column>
                <el-table-column label="增量收益" min-width="110"><template #default="{ row }"><span :class="Number(row.incremental_revenue_eur) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(row.incremental_revenue_eur) }}</span></template></el-table-column>
                <el-table-column label="循环" min-width="80"><template #default="{ row }">{{ formatNumber(row.cycle_equivalent_count, 1) }}</template></el-table-column>
                <el-table-column label="Pareto" min-width="80"><template #default="{ row }">{{ isTrue(row.pareto_front) ? '是' : '-' }}</template></el-table-column>
              </el-table>
            </ChartCard>
          </section>
        </el-tab-pane>

        <el-tab-pane label="策略治理" name="governance" lazy>
          <section v-if="scorecard.length" class="tab-stack">
            <div class="section-title">
              <span>Stage13 原型治理</span>
              <h3>原型容量策略治理对照</h3>
            </div>
            <div class="strategy-row">
              <div v-for="strategy in scorecard" :key="strategy.scenario_id" class="strategy-card glass-card" :class="decisionClass(strategy.governance_decision)">
                <div class="sc-header">
                  <span class="sc-decision" :class="decisionClass(strategy.governance_decision)">{{ strategy.governance_decision }}</span>
                  <span class="sc-score display-number">{{ formatNumber(strategy.governance_score, 1) }}</span>
                </div>
                <h4>{{ strategy.scenario_id }}</h4>
                <p class="sc-type">{{ strategy.strategy_type }}</p>
                <div class="sc-metrics">
                  <div><span>收益</span><strong>{{ formatCurrency(strategy.total_storage_revenue_eur) }}</strong></div>
                  <div><span>增量收益</span><strong :class="Number(strategy.incremental_revenue_eur) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(strategy.incremental_revenue_eur) }}</strong></div>
                  <div><span>循环</span><strong>{{ formatNumber(strategy.cycle_equivalent_count, 1) }}</strong></div>
                  <div><span>平均 SOC</span><strong>{{ formatPercent(strategy.mean_soc) }}</strong></div>
                </div>
                <p class="sc-reason">{{ strategy.decision_reason }}</p>
              </div>
            </div>

            <div class="chart-row">
              <ChartCard title="策略评分对比">
                <v-chart class="chart" :option="scoreBarOption" theme="dark-tech" autoresize />
              </ChartCard>
              <ChartCard title="三维评分雷达">
                <v-chart class="chart" :option="radarOption" theme="dark-tech" autoresize />
              </ChartCard>
            </div>
          </section>
          <PageState v-else type="empty" title="暂无策略治理评分" message="当前接口未返回 Stage13 策略治理评分。" />
        </el-tab-pane>
      </el-tabs>
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
import PageState from '../components/PageState.vue'
import { buildGovernanceRadarOption, buildRawhideRevenueOption, buildScoreBarOption } from '../charts/dispatchCharts'
import {
  fetchGovernanceScorecard,
  fetchRawhideDegradationMetrics,
  fetchRawhideDispatchMetrics,
  fetchRawhideReport,
  fetchRawhideSensitivityMetrics,
} from '../services/dispatchService'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, BarChart, RadarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent])

const scorecard = ref([])
const rawhideReport = ref(null)
const rawhideDispatchMetrics = ref([])
const rawhideSensitivityMetrics = ref([])
const rawhideDegradationMetrics = ref([])
const activeTab = ref('revenue')
const loading = ref(false)
const error = ref(null)

const scoreBarOption = computed(() => buildScoreBarOption(scorecard.value))
const radarOption = computed(() => buildGovernanceRadarOption(scorecard.value))
const rawhideRevenueOption = computed(() => buildRawhideRevenueOption(rawhideDispatchMetrics.value))
const rawhideAvailable = computed(() => Boolean(rawhideReport.value && rawhideDispatchMetrics.value.length))
const referenceSite = computed(() => rawhideReport.value?.reference_site || {})
const rawhideScaling = computed(() => rawhideReport.value?.scaling || {})
const bestPareto = computed(() => rawhideReport.value?.recommended_pareto_config || paretoRows.value[0] || {})
const degradationRecommended = computed(() => rawhideReport.value?.degradation_recommended_metrics || rawhideDegradationMetrics.value.find(item => item.scenario === 'rolling_with_rainflow_degradation') || {})
const rollingMetric = computed(() => rawhideDispatchMetrics.value.find(item => item.scenario === 'rolling_optimization') || {})
const stage11Metric = computed(() => rawhideDispatchMetrics.value.find(item => item.scenario === 'stage11_best_threshold_q40_q95') || {})
const stage10Metric = computed(() => rawhideDispatchMetrics.value.find(item => item.scenario === 'stage10_fixed_threshold') || {})
const paretoRows = computed(() => rawhideSensitivityMetrics.value.filter(item => isTrue(item.pareto_front)).slice(0, 6))
const dispatchInsight = computed(() => {
  const netRevenue = Number(degradationRecommended.value.net_incremental_revenue_eur)
  const rollingRevenue = Number(rollingMetric.value.incremental_revenue_eur)
  const paretoName = bestPareto.value.config_id || '数据缺失'
  return {
    title: rawhideAvailable.value
      ? `Rawhide 滚动策略增量收益为 ${formatCurrency(rollingRevenue)}，退化后净增量为 ${formatCurrency(netRevenue)}。`
      : '当前缺少 Rawhide 实站仿真数据，页面仅能展示已返回的原型策略治理结果。',
    tone: Number.isFinite(netRevenue) ? (netRevenue >= 0 ? 'positive' : 'warning') : 'warning',
    items: [
      `推荐 Pareto 配置：${paretoName}。`,
      `退化成本：${formatCurrency(degradationRecommended.value.degradation_cost_eur)}；SOH：${formatPercent(degradationRecommended.value.soh_start)} -> ${formatPercent(degradationRecommended.value.soh_end)}。`,
      `Stage13 策略治理记录：${scorecard.value.length} 条。`,
    ],
  }
})
const rawhideKpis = computed(() => [
  { label: 'Rolling 增量收益', value: formatCurrency(rollingMetric.value.incremental_revenue_eur), hint: 'Stage12 rolling optimization', className: Number(rollingMetric.value.incremental_revenue_eur || 0) >= 0 ? 'positive' : 'negative' },
  { label: 'Stage11 离线上界', value: formatCurrency(stage11Metric.value.incremental_revenue_eur), hint: 'best threshold q40/q95', className: 'positive' },
  { label: 'Stage10 固定阈值', value: formatCurrency(stage10Metric.value.incremental_revenue_eur), hint: 'failure baseline', className: Number(stage10Metric.value.incremental_revenue_eur || 0) >= 0 ? 'positive' : 'negative' },
  { label: '退化后净增量', value: formatCurrency(degradationRecommended.value.net_incremental_revenue_eur), hint: 'rainflow degradation replay', className: Number(degradationRecommended.value.net_incremental_revenue_eur || 0) >= 0 ? 'positive' : 'negative' },
  { label: 'SOH 变化', value: `${formatPercent(degradationRecommended.value.soh_start)} -> ${formatPercent(degradationRecommended.value.soh_end)}`, hint: 'cycle + calendar degradation', className: 'neutral' },
  { label: '推荐 Pareto', value: `${formatKwh(bestPareto.value.capacity_kwh)} / ${formatKw(bestPareto.value.max_discharge_kw)}`, hint: bestPareto.value.config_id || 'configuration scan', className: 'neutral' },
])

function decisionClass(decision) {
  const map = { reject: 'decision-reject', pilot_candidate: 'decision-pilot', baseline: 'decision-baseline', analysis_upper_bound: 'decision-upper' }
  return map[decision] || ''
}
function isTrue(value) {
  return value === true || value === 'True' || value === 'true' || value === 1 || value === '1'
}
function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}
function formatNumber(value, digits = 2) {
  const n = toNumber(value)
  return n === null ? 'N/A' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}
function formatCurrency(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `€${n.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`
}
function formatPercent(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${(n * 100).toFixed(1)}%`
}
function formatKw(value) {
  const n = toNumber(value)
  if (n === null) return 'N/A'
  return n >= 1000 ? `${(n / 1000).toFixed(1)} MW` : `${n.toFixed(0)} kW`
}
function formatMw(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${(n / 1000).toFixed(1)} MW_AC`
}
function formatKwh(value) {
  const n = toNumber(value)
  if (n === null) return 'N/A'
  return n >= 1000 ? `${(n / 1000).toFixed(1)} MWh` : `${n.toFixed(0)} kWh`
}
async function optionalRequest(request) {
  try {
    return await request()
  } catch {
    return null
  }
}
async function loadScorecard() {
  loading.value = true
  error.value = null
  try {
    const [scorecardData, reportData, dispatchData, sensitivityData, degradationData] = await Promise.all([
      optionalRequest(fetchGovernanceScorecard),
      optionalRequest(fetchRawhideReport),
      optionalRequest(fetchRawhideDispatchMetrics),
      optionalRequest(fetchRawhideSensitivityMetrics),
      optionalRequest(fetchRawhideDegradationMetrics),
    ])
    scorecard.value = scorecardData || []
    rawhideReport.value = reportData
    rawhideDispatchMetrics.value = dispatchData || []
    rawhideSensitivityMetrics.value = sensitivityData || []
    rawhideDegradationMetrics.value = degradationData || []
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadScorecard)
</script>

<style scoped>
.dispatch { display: flex; flex-direction: column; gap: var(--space-lg); }
.dispatch-tabs { min-width: 0; }
.tab-stack { display: flex; flex-direction: column; gap: var(--space-lg); }
:deep(.el-tabs__nav-wrap::after) { background: var(--border-glass); }
:deep(.el-tabs__item) { color: var(--text-secondary); font-weight: 700; }
:deep(.el-tabs__item.is-active) { color: var(--accent-cyan); }
:deep(.el-tabs__active-bar) { background: var(--accent-cyan); }
.rawhide-hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 0.7fr); gap: var(--space-lg); padding: var(--space-xl); }
.kicker,
.section-title span,
.pareto-summary span { color: var(--accent-cyan); display: block; font-size: 11px; font-weight: 800; letter-spacing: 0.08em; margin-bottom: 6px; text-transform: uppercase; }
.rawhide-hero h2 { color: var(--text-primary); font-size: 26px; line-height: 1.2; margin-bottom: 8px; }
.rawhide-hero p,
.chart-note,
.boundary-panel p,
.degradation-main p,
.pareto-summary p { color: var(--text-secondary); font-size: 13px; line-height: 1.65; }
.rawhide-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.rawhide-facts div,
.degradation-list div { background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-sm); padding: 12px; }
.rawhide-facts span,
.rawhide-metric span,
.degradation-main span,
.degradation-list span { color: var(--text-secondary); display: block; font-size: 11px; margin-bottom: 4px; }
.rawhide-facts strong,
.degradation-list strong { color: var(--text-primary); display: block; font-size: 15px; overflow-wrap: anywhere; }
.boundary-panel { border-color: rgba(255, 167, 38, 0.28); padding: var(--space-md) var(--space-lg); }
.boundary-panel strong { color: var(--accent-orange); display: block; font-size: 13px; margin-bottom: 4px; }
.rawhide-metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-lg); }
.rawhide-metric { padding: var(--space-lg); }
.rawhide-metric strong { display: block; font-size: 22px; line-height: 1.25; overflow-wrap: anywhere; }
.rawhide-metric small { color: var(--text-tertiary); display: block; font-size: 11px; margin-top: 8px; }
.positive { color: var(--accent-green) !important; }
.negative { color: var(--accent-red) !important; }
.neutral { color: var(--text-primary) !important; }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }
.chart { height: 320px; width: 100%; }
.degradation-panel { display: grid; gap: var(--space-md); }
.degradation-main { border-bottom: 1px solid var(--border-glass); padding-bottom: var(--space-md); }
.degradation-main strong { display: block; font-size: 28px; line-height: 1.2; margin: 2px 0 6px; }
.degradation-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.pareto-summary { border-bottom: 1px solid var(--border-glass); margin-bottom: var(--space-md); padding-bottom: var(--space-md); }
.pareto-summary strong { color: var(--text-primary); display: block; font-size: 16px; margin-bottom: 4px; }
.section-title { margin-top: var(--space-sm); }
.section-title h3 { color: var(--text-primary); font-size: 18px; font-weight: 700; }
.strategy-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--space-lg); }
.strategy-card { border-top: 3px solid var(--text-tertiary); padding: var(--space-lg); }
.strategy-card.decision-upper { border-top-color: var(--accent-green); }
.strategy-card.decision-pilot { border-top-color: var(--accent-cyan); }
.strategy-card.decision-baseline { border-top-color: var(--accent-orange); }
.strategy-card.decision-reject { border-top-color: var(--accent-red); }
.sc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.sc-decision { border-radius: var(--radius-full); background: rgba(255,255,255,0.08); color: var(--text-secondary); font-size: 11px; font-weight: 700; padding: 2px 10px; text-transform: uppercase; }
.sc-score { font-size: 24px; }
.strategy-card h4 { color: var(--text-primary); font-size: 13px; margin-bottom: 2px; }
.sc-type { color: var(--text-tertiary); font-size: 11px; margin-bottom: 12px; }
.sc-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.sc-metrics div { display: flex; flex-direction: column; }
.sc-metrics span { color: var(--text-tertiary); font-size: 10px; }
.sc-metrics strong { color: var(--text-primary); font-size: 14px; }
.sc-reason { border-top: 1px solid var(--border-glass); color: var(--text-secondary); font-size: 11px; line-height: 1.5; padding-top: 10px; }

@media (max-width: 1199px) {
  .rawhide-hero,
  .chart-row { grid-template-columns: 1fr; }
}

@media (max-width: 767px) {
  .rawhide-hero,
  .boundary-panel,
  .rawhide-metric { padding: var(--space-md); }
  .rawhide-facts,
  .degradation-list,
  .sc-metrics { grid-template-columns: 1fr; }
  .chart { height: 300px; }
}
</style>
