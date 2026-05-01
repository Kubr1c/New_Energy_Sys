<template>
  <div class="data-explorer">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载数据运维"
      message="正在读取数据质量、特征重要性和任务命令列表。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="数据运维加载失败"
      :message="error.message"
      retryable
      @retry="loadDataExplorer"
    />
    <PageState
      v-else-if="!hasExplorerData"
      type="empty"
      title="暂无数据运维结果"
      message="当前接口没有返回质量报告、特征重要性或任务命令。"
      retryable
      @retry="loadDataExplorer"
    />
    <template v-else>
      <MetricGrid :items="qualityCards" min-width="240px" />

      <ChartCard title="特征重要性 Top 20 — Feature Importance">
        <PageState
          v-if="!hasPositiveFeatures"
          type="empty"
          title="暂无非零特征重要性"
          message="接口已返回特征列表，但 importance/gain 均为 0 或缺失，请检查 Stage4 特征重要性产物。"
        />
        <v-chart v-else class="feat-chart" :option="featureChartOption" theme="dark-tech" autoresize />
      </ChartCard>

      <PageSection title="模型训练 / 调度仿真触发 — Task Runner">
        <div class="task-grid">
          <div v-for="cmd in commands" :key="cmd.command_id" class="task-item">
            <div class="task-copy">
              <strong>{{ cmd.label }}</strong>
              <span>{{ cmd.command_id }} · Admin only</span>
            </div>
            <el-button size="small" type="primary" :loading="runningTasks[cmd.command_id]" @click="submitTask(cmd.command_id)">
              Run
            </el-button>
          </div>
        </div>
        <p v-if="taskError" class="task-error">{{ taskError.message }}</p>
        <div v-if="taskHistory.length" class="task-history">
          <h4>Recent Tasks</h4>
          <div v-for="task in taskHistory" :key="task.task_id" class="task-record">
            <span>{{ task.command }}</span>
            <strong :class="task.status">{{ task.status }}</strong>
            <em>{{ formatDuration(task) }}</em>
          </div>
        </div>
      </PageSection>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildFeatureImportanceOption } from '../charts/dataExplorerCharts'
import { fetchDataExplorerBundle, fetchTasks, fetchTaskStatus, submitTaskCommand } from '../services/dataExplorerService'
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
const taskPolls = new Map()

const hasExplorerData = computed(() => Boolean(quality.value) || features.value.length > 0 || commands.value.length > 0)
const hasPositiveFeatures = computed(() => features.value.some(feature => Number(feature.importance ?? feature.gain ?? 0) > 0))
const featureChartOption = computed(() => buildFeatureImportanceOption(features.value))

const qualityCards = computed(() => [
  { label: '样本数 Samples', value: valueOrIssue(quality.value?.rows?.final_cleaned), icon: '📊', iconType: 'text', gradient: 'var(--gradient-cyan)' },
  { label: '字段数 Columns', value: valueOrIssue(quality.value?.schema?.column_count ?? quality.value?.columns?.total), icon: '📋', iconType: 'text', gradient: 'var(--gradient-green)' },
  { label: '时间范围 Period', value: periodText.value, icon: '🗓', iconType: 'text', gradient: 'var(--gradient-orange)' },
  { label: '覆盖率 Coverage', value: coverageText.value, icon: '✓', iconType: 'text', gradient: 'var(--gradient-purple)' },
])

const periodText = computed(() => {
  const start = quality.value?.time_alignment?.min_timestamp || quality.value?.date_range?.start
  const end = quality.value?.time_alignment?.max_timestamp || quality.value?.date_range?.end
  return start && end ? `${String(start).slice(0, 10)} ~ ${String(end).slice(0, 10)}` : '字段缺失'
})

const coverageText = computed(() => {
  const coverage = Number(quality.value?.time_alignment?.target_hour_coverage)
  return Number.isFinite(coverage) ? `${(coverage * 100).toFixed(1)}%` : '字段缺失'
})

function valueOrIssue(value) {
  return value === null || value === undefined || value === '' ? '字段缺失' : value
}

function formatDuration(task) {
  if (!task.started_at || !task.finished_at) return '-'
  return `${(task.finished_at - task.started_at).toFixed(1)}s`
}

async function loadTasks() {
  try {
    taskHistory.value = await fetchTasks()
  } catch (e) {
    taskError.value = e.normalized || normalizeApiError(e)
  }
}

async function submitTask(cmdId) {
  runningTasks.value[cmdId] = true
  taskError.value = null
  try {
    const { task_id: taskId } = await submitTaskCommand(cmdId)
    const poll = setInterval(async () => {
      try {
        const status = await fetchTaskStatus(taskId)
        if (status.status === 'completed' || status.status === 'failed') {
          clearInterval(poll)
          taskPolls.delete(taskId)
          runningTasks.value[cmdId] = false
          loadTasks()
        }
      } catch (e) {
        clearInterval(poll)
        taskPolls.delete(taskId)
        runningTasks.value[cmdId] = false
        taskError.value = e.normalized || normalizeApiError(e)
      }
    }, 2000)
    taskPolls.set(taskId, poll)
  } catch (e) {
    runningTasks.value[cmdId] = false
    taskError.value = e.normalized || normalizeApiError(e)
  }
}

async function loadDataExplorer() {
  loading.value = true
  error.value = null
  taskError.value = null
  try {
    const bundle = await fetchDataExplorerBundle()
    quality.value = bundle.quality || {}
    features.value = bundle.features || []
    commands.value = bundle.commands || []
    await loadTasks()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadDataExplorer)
onUnmounted(() => {
  for (const poll of taskPolls.values()) clearInterval(poll)
  taskPolls.clear()
})
</script>

<style scoped>
.data-explorer { display: flex; flex-direction: column; gap: var(--space-lg); }
.feat-chart { height: 420px; width: 100%; }
.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}
.task-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--bg-input);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}
.task-copy { display: flex; flex-direction: column; min-width: 0; }
.task-copy strong { color: var(--text-primary); font-size: 13px; }
.task-copy span { color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
.task-error { color: var(--accent-red); font-size: 13px; margin-top: var(--space-md); }
.task-history { margin-top: var(--space-lg); border-top: 1px solid var(--border-glass); padding-top: var(--space-md); }
.task-history h4 { color: var(--text-secondary); font-size: 13px; margin-bottom: 8px; }
.task-record {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px 80px;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 12px;
  padding: 6px 0;
}
.task-record strong.completed { color: var(--accent-green); }
.task-record strong.failed { color: var(--accent-red); }
.task-record strong.running { color: var(--accent-cyan); }
.task-record em { color: var(--text-tertiary); font-style: normal; }

@media (max-width: 767px) {
  .feat-chart { height: 340px; }
  .task-grid { grid-template-columns: 1fr; }
  .task-item,
  .task-record { align-items: flex-start; display: flex; flex-direction: column; }
}
</style>
