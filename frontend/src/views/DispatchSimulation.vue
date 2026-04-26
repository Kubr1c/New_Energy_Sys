<template>
  <div class="dispatch">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载调度仿真"
      message="正在读取策略治理评分与调度收益指标。"
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
      v-else-if="!scorecard.length"
      type="empty"
      title="暂无调度策略数据"
      message="当前接口没有返回可展示的策略评分。"
      retryable
      @retry="loadScorecard"
    />
    <template v-else>
    <!-- Strategy Cards -->
    <div class="strategy-row">
      <div v-for="(s, i) in scorecard" :key="s.scenario_id" class="strategy-card glass-card animate-fade-in-up" :class="['animate-delay-' + (i + 1), decisionClass(s.governance_decision)]">
        <div class="sc-header">
          <span class="sc-decision" :class="decisionClass(s.governance_decision)">{{ s.governance_decision }}</span>
          <span class="sc-score display-number">{{ Number(s.governance_score).toFixed(1) }}</span>
        </div>
        <h4 class="sc-name">{{ s.scenario_id }}</h4>
        <p class="sc-type">{{ s.strategy_type }}</p>
        <div class="sc-metrics">
          <div class="sc-metric"><span class="sc-ml">收益 Revenue</span><span class="sc-mv display-number">€{{ Number(s.total_storage_revenue_eur).toFixed(2) }}</span></div>
          <div class="sc-metric"><span class="sc-ml">增量 Incr.</span><span class="sc-mv display-number" :class="Number(s.incremental_revenue_eur) >= 0 ? 'positive' : 'negative'">€{{ Number(s.incremental_revenue_eur).toFixed(3) }}</span></div>
          <div class="sc-metric"><span class="sc-ml">循环 Cycles</span><span class="sc-mv display-number">{{ Number(s.cycle_equivalent_count).toFixed(1) }}</span></div>
          <div class="sc-metric"><span class="sc-ml">均值SOC</span><span class="sc-mv display-number">{{ (Number(s.mean_soc) * 100).toFixed(1) }}%</span></div>
        </div>
        <p class="sc-reason">{{ s.decision_reason }}</p>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="chart-row">
      <div class="glass-card chart-card animate-fade-in-up animate-delay-3">
        <h3>策略评分对比 — Strategy Scores</h3>
        <v-chart class="chart" :option="scoreBarOption" theme="dark-tech" autoresize />
      </div>
      <div class="glass-card chart-card animate-fade-in-up animate-delay-4">
        <h3>三维评分雷达 — Governance Radar</h3>
        <v-chart class="chart" :option="radarOption" theme="dark-tech" autoresize />
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

const scorecard = ref([])
const loading = ref(false)
const error = ref(null)

function decisionClass(d) {
  const map = { reject: 'decision-reject', pilot_candidate: 'decision-pilot', baseline: 'decision-baseline', analysis_upper_bound: 'decision-upper' }
  return map[d] || ''
}

const scoreBarOption = computed(() => {
  if (!scorecard.value.length) return {}
  const items = scorecard.value
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['Economic', 'Constraint', 'Risk'], top: 0 },
    grid: { left: 140, right: 30, top: 40, bottom: 20 },
    xAxis: { type: 'value', max: 100 },
    yAxis: { type: 'category', data: items.map(s => s.scenario_id), axisLabel: { fontSize: 10 } },
    series: [
      { name: 'Economic', type: 'bar', stack: 'score', data: items.map(s => (Number(s.economic_score) / 3).toFixed(1)), itemStyle: { color: '#00d4ff' } },
      { name: 'Constraint', type: 'bar', stack: 'score', data: items.map(s => (Number(s.constraint_score) / 3).toFixed(1)), itemStyle: { color: '#00f5a0' } },
      { name: 'Risk', type: 'bar', stack: 'score', data: items.map(s => (Number(s.risk_score) / 3).toFixed(1)), itemStyle: { color: '#ffa726' } },
    ],
  }
})

const radarOption = computed(() => {
  if (!scorecard.value.length) return {}
  const items = scorecard.value
  const indicator = [{ name: 'Economic', max: 100 }, { name: 'Constraint', max: 100 }, { name: 'Risk', max: 100 }]
  const colors = ['#00d4ff', '#00f5a0', '#ffa726', '#ff5252']
  return {
    legend: { data: items.map(s => s.scenario_id), top: 0, textStyle: { fontSize: 10 } },
    radar: { indicator, center: ['50%', '55%'], radius: '55%' },
    series: [{ type: 'radar', data: items.map((s, i) => ({
      name: s.scenario_id,
      value: [Number(s.economic_score), Number(s.constraint_score), Number(s.risk_score)],
      lineStyle: { color: colors[i] },
      itemStyle: { color: colors[i] },
      areaStyle: { color: colors[i], opacity: 0.12 },
    })) }],
  }
})

async function loadScorecard() {
  loading.value = true
  error.value = null
  try {
    const res = await api.get('/api/governance/scorecard')
    scorecard.value = res.data
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadScorecard)
</script>

<style scoped>
.dispatch { display: flex; flex-direction: column; gap: var(--space-xl); }

.strategy-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--space-lg); }
.strategy-card { padding: var(--space-lg); position: relative; overflow: hidden; }
.strategy-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: var(--text-tertiary);
}
.strategy-card.decision-upper::before { background: var(--accent-green); }
.strategy-card.decision-pilot::before { background: var(--accent-cyan); }
.strategy-card.decision-baseline::before { background: var(--accent-orange); }
.strategy-card.decision-reject::before { background: var(--accent-red); }

.sc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.sc-decision { font-size: 11px; padding: 2px 10px; border-radius: var(--radius-full); font-weight: 600; text-transform: uppercase; }
.sc-decision.decision-upper { background: rgba(0,245,160,0.12); color: var(--accent-green); }
.sc-decision.decision-pilot { background: rgba(0,212,255,0.12); color: var(--accent-cyan); }
.sc-decision.decision-baseline { background: rgba(255,167,38,0.12); color: var(--accent-orange); }
.sc-decision.decision-reject { background: rgba(255,82,82,0.12); color: var(--accent-red); }
.sc-score { font-size: 24px; }

.sc-name { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 2px; }
.sc-type { font-size: 11px; color: var(--text-tertiary); margin-bottom: 12px; }
.sc-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.sc-metric { display: flex; flex-direction: column; }
.sc-ml { font-size: 10px; color: var(--text-tertiary); }
.sc-mv { font-size: 14px; }
.sc-mv.positive { color: var(--accent-green); }
.sc-mv.negative { color: var(--accent-red); }
.sc-reason { font-size: 11px; color: var(--text-secondary); line-height: 1.5; border-top: 1px solid var(--border-glass); padding-top: 10px; }

.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }
.chart-card { padding: var(--space-lg); }
.chart-card h3 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-md); }
.chart { height: 340px; width: 100%; }

@media (max-width: 1199px) {
  .chart-row { grid-template-columns: 1fr; }
  .strategy-card, .chart-card { min-width: 0; }
}

@media (max-width: 767px) {
  .dispatch { gap: var(--space-lg); }
  .strategy-row { grid-template-columns: 1fr; }
  .sc-metrics { grid-template-columns: 1fr; }
  .chart { height: 320px; }
}
</style>
