<template>
  <div class="data-explorer">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载数据探索"
      message="正在读取数据质量、特征重要性和任务命令列表。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="数据探索加载失败"
      :message="error.message"
      retryable
      @retry="loadDataExplorer"
    />
    <PageState
      v-else-if="!hasExplorerData"
      type="empty"
      title="暂无数据探索结果"
      message="当前接口没有返回数据质量、特征重要性或任务命令。"
      retryable
      @retry="loadDataExplorer"
    />
    <template v-else>
    <!-- Data Quality -->
    <div class="quality-row">
      <div class="glass-card quality-card animate-fade-in-up" v-for="(item, i) in qualityCards" :key="item.label" :class="'animate-delay-' + (i+1)">
        <div class="q-icon" :style="{ background: item.gradient }">{{ item.emoji }}</div>
        <div class="q-body">
          <div class="q-value display-number">{{ item.value }}</div>
          <div class="q-label">{{ item.label }}</div>
        </div>
      </div>
    </div>

    <!-- Feature Importance -->
    <div class="glass-card feat-card animate-fade-in-up animate-delay-3">
      <h3>🔬 特征重要性 Top 20 — Feature Importance</h3>
      <v-chart class="feat-chart" :option="featureChartOption" theme="dark-tech" autoresize />
    </div>

    <!-- Task Runner -->
    <div class="glass-card task-card animate-fade-in-up animate-delay-4">
      <h3>🚀 模型训练 / 调度仿真触发 — Task Runner</h3>
      <div class="task-grid">
        <div v-for="cmd in commands" :key="cmd.command_id" class="task-item">
          <span class="task-label">{{ cmd.label }}</span>
          <el-button size="small" type="primary" :loading="runningTasks[cmd.command_id]" @click="submitTask(cmd.command_id)">
            Run
          </el-button>
        </div>
      </div>
      <div v-if="taskHistory.length" class="task-history">
        <h4>Recent Tasks</h4>
        <div v-for="t in taskHistory" :key="t.task_id" class="task-record">
          <span class="tr-cmd">{{ t.command }}</span>
          <span class="tr-status" :class="t.status">{{ t.status }}</span>
          <span class="tr-time">{{ formatDuration(t) }}</span>
        </div>
      </div>
      <p v-if="taskError" class="task-error">{{ taskError.message }}</p>
    </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import api from '../utils/api'
import PageState from '../components/PageState.vue'
import { normalizeApiError } from '../utils/api'

use([CanvasRenderer, BarChart, TitleComponent, TooltipComponent, GridComponent])

const quality = ref({})
const features = ref([])
const commands = ref([])
const taskHistory = ref([])
const runningTasks = ref({})
const loading = ref(false)
const error = ref(null)
const taskError = ref(null)

const hasExplorerData = computed(() => {
  return Boolean(quality.value?.summary) || features.value.length > 0 || commands.value.length > 0
})

const qualityCards = computed(() => {
  const q = quality.value
  return [
    { label: '样本数 Samples', value: q?.summary?.total_rows ?? '—', emoji: '📊', gradient: 'var(--gradient-cyan)' },
    { label: '字段数 Columns', value: q?.summary?.total_columns ?? '—', emoji: '📋', gradient: 'var(--gradient-green)' },
    { label: '时间范围 Period', value: q?.summary?.date_range_start ? `${q.summary.date_range_start?.slice(0,10)} ~ ${q.summary.date_range_end?.slice(0,10)}` : '—', emoji: '📅', gradient: 'var(--gradient-orange)' },
    { label: '覆盖率 Coverage', value: q?.summary?.hour_coverage_pct ? `${(q.summary.hour_coverage_pct * 100).toFixed(1)}%` : '—', emoji: '✅', gradient: 'var(--gradient-purple)' },
  ]
})

const featureChartOption = computed(() => {
  if (!features.value.length) return {}
  const top20 = features.value.slice(0, 20).reverse()
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 200, right: 30, top: 10, bottom: 20 },
    xAxis: { type: 'value', name: 'Importance' },
    yAxis: { type: 'category', data: top20.map(f => f.feature || f.name || ''), axisLabel: { fontSize: 11 } },
    series: [{ type: 'bar', data: top20.map((f, i) => ({ value: Number(f.importance || f.gain || 0).toFixed(4), itemStyle: { color: `hsl(${190 + i * 4}, 80%, 55%)` } })), barMaxWidth: 18 }],
  }
})

function formatDuration(t) {
  if (!t.started_at || !t.finished_at) return '—'
  return `${(t.finished_at - t.started_at).toFixed(1)}s`
}

async function submitTask(cmdId) {
  runningTasks.value[cmdId] = true
  taskError.value = null
  try {
    const res = await api.post('/api/tasks/submit', { command_id: cmdId })
    const taskId = res.data.task_id
    // Poll until done
    const poll = setInterval(async () => {
      const st = await api.get(`/api/tasks/${taskId}`)
      if (st.data.status === 'completed' || st.data.status === 'failed') {
        clearInterval(poll)
        runningTasks.value[cmdId] = false
        loadTasks()
      }
    }, 2000)
  } catch (e) {
    runningTasks.value[cmdId] = false
    taskError.value = e.normalized || normalizeApiError(e)
  }
}

async function loadTasks() {
  try { const r = await api.get('/api/tasks'); taskHistory.value = r.data } catch (e) { taskError.value = e.normalized || normalizeApiError(e) }
}

async function loadDataExplorer() {
  loading.value = true
  error.value = null
  taskError.value = null
  try {
    const [qRes, fRes, cRes] = await Promise.all([
      api.get('/api/data/quality'),
      api.get('/api/features/importance', { params: { top_n: 20 } }),
      api.get('/api/tasks/commands'),
    ])
    quality.value = qRes.data || {}
    features.value = fRes.data || []
    commands.value = cRes.data || []
    await loadTasks()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadDataExplorer)
</script>

<style scoped>
.data-explorer { display: flex; flex-direction: column; gap: var(--space-xl); }

.quality-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-lg); }
.quality-card { display: flex; align-items: center; gap: var(--space-md); padding: 20px 24px; }
.q-icon { width: 44px; height: 44px; border-radius: var(--radius-md); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
.q-value { font-size: 18px; color: var(--text-primary); }
.q-label { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }

.feat-card { padding: var(--space-lg); }
.feat-card h3 { font-size: 15px; font-weight: 600; margin-bottom: var(--space-md); }
.feat-chart { height: 460px; width: 100%; }

.task-card { padding: var(--space-lg); }
.task-card h3 { font-size: 15px; font-weight: 600; margin-bottom: var(--space-md); }
.task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.task-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; background: var(--bg-input); border-radius: var(--radius-md); border: 1px solid var(--border-glass); }
.task-label { font-size: 13px; color: var(--text-primary); }

.task-history { margin-top: var(--space-lg); border-top: 1px solid var(--border-glass); padding-top: var(--space-md); }
.task-history h4 { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.task-record { display: flex; gap: var(--space-lg); padding: 6px 0; font-size: 12px; }
.tr-cmd { color: var(--text-primary); min-width: 150px; }
.tr-status { font-weight: 600; }
.tr-status.completed { color: var(--accent-green); }
.tr-status.failed { color: var(--accent-red); }
.tr-status.running { color: var(--accent-cyan); }
.tr-status.pending { color: var(--text-tertiary); }
.tr-time { color: var(--text-secondary); }
.task-error { color: var(--accent-red); font-size: 13px; margin-top: var(--space-md); }

@media (max-width: 1199px) {
  .quality-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .feat-card, .task-card, .quality-card { min-width: 0; }
}

@media (max-width: 767px) {
  .data-explorer { gap: var(--space-lg); }
  .quality-row, .task-grid { grid-template-columns: 1fr; }
  .quality-card { padding: 16px; }
  .feat-chart { height: 360px; }
  .task-item, .task-record { align-items: flex-start; flex-direction: column; gap: 8px; }
  .tr-cmd { min-width: 0; }
}
</style>
