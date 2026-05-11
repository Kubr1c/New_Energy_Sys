<template>
  <div class="data-explorer">
    <PageState
      v-if="loading"
      type="loading"
      title="正在加载数据管理"
      message="正在读取数据质量、特征重要性和任务命令列表。"
    />
    <PageState
      v-else-if="error"
      type="error"
      title="数据管理加载失败"
      :message="error.message"
      retryable
      @retry="loadDataExplorer"
    />
    <PageState
      v-else-if="!hasExplorerData"
      type="empty"
      title="暂无数据管理结果"
      message="当前接口没有返回质量报告、特征重要性或任务命令。"
      retryable
      @retry="loadDataExplorer"
    />
    <template v-else>
      <InsightSummary :title="dataInsight.title" :items="dataInsight.items" :tone="dataInsight.tone" />

      <MetricGrid :items="qualityCards" min-width="240px" />

      <PageSection title="字段完整性检查">
        <div class="integrity-grid">
          <div v-for="item in integritySummary" :key="item.label" class="integrity-item">
            <span>{{ item.label }}</span>
            <strong :class="item.ok ? 'positive' : 'negative'">{{ item.value }}</strong>
            <small>{{ item.hint }}</small>
          </div>
        </div>
        <el-table v-if="positiveMissingRows.length" :data="positiveMissingRows" stripe style="width: 100%">
          <el-table-column prop="fieldLabel" label="字段名称" min-width="180" />
          <el-table-column prop="missingCount" label="缺失数量" min-width="110" align="right" />
          <el-table-column prop="missingRatio" label="缺失比例" min-width="110" align="right" />
          <el-table-column prop="fieldType" label="字段类型" min-width="130" />
          <el-table-column prop="suggestion" label="建议处理方式" min-width="260" />
        </el-table>
        <PageState
          v-else
          type="empty"
          title="缺失字段数 0"
          message="当前质量报告未发现字段缺失，时间字段、目标字段和特征字段均可用于展示。"
        />
      </PageSection>

      <ChartCard title="特征重要性 Top20">
        <p class="section-help">特征重要性用于展示各输入变量对预测结果的相对贡献。图中使用中文字段名，原始字段名保留在提示信息中用于排查。</p>
        <PageState
          v-if="!hasPositiveFeatures"
          type="empty"
          title="暂无特征重要性结果"
          message="当前模型暂未生成特征重要性结果，请先完成模型训练或检查模型类型是否支持特征重要性分析。"
        />
        <v-chart v-else class="feat-chart" :option="featureChartOption" theme="dark-tech" autoresize />
      </ChartCard>

      <PageSection title="模型训练 / 调度仿真">
        <div class="task-grid">
          <div v-for="cmd in displayCommands" :key="cmd.command_id" class="task-item">
            <div class="task-copy">
              <strong>{{ cmd.label }}</strong>
              <span>{{ cmd.description }}</span>
            </div>
            <el-button
              size="small"
              type="primary"
              :loading="isTaskRunning(cmd.command_id)"
              :disabled="isSameTypeRunning(cmd)"
              @click="submitTask(cmd.command_id)"
            >
              {{ taskButtonText(cmd) }}
            </el-button>
          </div>
        </div>

        <div v-if="activeTask" class="task-feedback glass-card" :class="activeTask.status">
          <div class="task-feedback-header">
            <strong>{{ activeTask.message }}</strong>
            <span>{{ taskStatusLabel(activeTask.status) }}</span>
          </div>
          <div class="task-meta">
            <span>开始时间：{{ formatTimestamp(activeTask.startedAt || activeTask.createdAt) }}</span>
            <span>耗时：{{ formatDuration(activeTask) }}</span>
            <span>最后更新时间：{{ formatTimestamp(activeTask.updatedAt) }}</span>
          </div>
          <el-collapse v-if="activeTask.errorDetail" class="task-log-collapse">
            <el-collapse-item title="查看错误详情" name="error">
              <pre>{{ activeTask.errorDetail }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>

        <div class="task-history">
          <h4>最近任务</h4>
          <el-table v-if="normalizedTaskHistory.length" :data="normalizedTaskHistory" size="small" stripe>
            <el-table-column label="任务名称" min-width="160">
              <template #default="{ row }">{{ taskLabel(row.command) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }"><strong :class="row.uiStatus">{{ taskStatusLabel(row.uiStatus) }}</strong></template>
            </el-table-column>
            <el-table-column label="开始时间" min-width="150">
              <template #default="{ row }">{{ formatTimestamp(row.started_at || row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="90">
              <template #default="{ row }">{{ formatTaskDuration(row) }}</template>
            </el-table-column>
            <el-table-column label="结果入口" width="120">
              <template #default="{ row }">
                <el-button link type="primary" @click="goTaskResult(row.command)">查看结果</el-button>
              </template>
            </el-table-column>
          </el-table>
          <p v-else class="section-help">暂无最近任务记录。</p>
        </div>
      </PageSection>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import InsightSummary from '../components/InsightSummary.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildFeatureImportanceOption, featureImportanceScore } from '../charts/dataExplorerCharts'
import { fetchDataExplorerBundle, fetchTasks, fetchTaskStatus, submitTaskCommand } from '../services/dataExplorerService'
import { normalizeApiError } from '../utils/api'
import { featureFieldLabel, taskLabel, taskType } from '../utils/displayLabels'

use([CanvasRenderer, BarChart, TitleComponent, TooltipComponent, GridComponent])

const TASK_TIMEOUT_MS = 10 * 60 * 1000
const POLL_INTERVAL_MS = 2000

const router = useRouter()
const quality = ref({})
const features = ref([])
const commands = ref([])
const taskHistory = ref([])
const taskStates = ref({})
const activeTaskKey = ref('')
const loading = ref(false)
const error = ref(null)
const taskPolls = new Map()

const hasExplorerData = computed(() => Boolean(quality.value) || features.value.length > 0 || commands.value.length > 0)
const hasPositiveFeatures = computed(() => features.value.some(feature => featureImportanceScore(feature) > 0))
const featureChartOption = computed(() => buildFeatureImportanceOption(features.value))
const topFeature = computed(() => [...features.value].sort((a, b) => featureImportanceScore(b) - featureImportanceScore(a))[0] || {})
const displayCommands = computed(() => commands.value.map(command => ({
  ...command,
  label: taskLabel(command.command_id),
  description: commandDescription(command.command_id),
  type: taskType(command.command_id),
})))

const missingRows = computed(() => {
  const missing = quality.value?.missing_values || {}
  const totalRows = Number(quality.value?.rows?.initial ?? quality.value?.rows?.final_cleaned ?? 0)
  return Object.entries(missing)
    .map(([field, stats]) => {
      const missingCount = Number(stats?.missing_count ?? stats?.before ?? stats?.count ?? 0)
      const ratioValue = Number(stats?.missing_ratio ?? stats?.ratio ?? (totalRows > 0 ? missingCount / totalRows : 0))
      return {
        field,
        fieldLabel: featureFieldLabel(field),
        missingCount,
        missingRatioValue: Number.isFinite(ratioValue) ? ratioValue : 0,
        missingRatio: `${((Number.isFinite(ratioValue) ? ratioValue : 0) * 100).toFixed(2)}%`,
        fieldType: fieldTypeLabel(field),
        suggestion: missingSuggestion(field, missingCount),
      }
    })
    .sort((a, b) => b.missingCount - a.missingCount)
})
const positiveMissingRows = computed(() => missingRows.value.filter(row => row.missingCount > 0 || row.missingRatioValue > 0))
const missingFieldCount = computed(() => positiveMissingRows.value.length)
const activeTask = computed(() => activeTaskKey.value ? taskStates.value[activeTaskKey.value] || null : null)
const normalizedTaskHistory = computed(() => taskHistory.value.map(task => ({ ...task, uiStatus: normalizeTaskStatus(task.status) })))
const dataInsight = computed(() => ({
  title: hasExplorerData.value
    ? `当前清洗后样本数为 ${valueOrIssue(quality.value?.rows?.final_cleaned)}，数据覆盖率为 ${coverageText.value}。`
    : '当前未读取到数据质量、特征重要性或任务命令结果。',
  tone: hasExplorerData.value ? 'positive' : 'warning',
  items: [
    `时间范围：${periodText.value}。`,
    `最高贡献特征：${featureFieldLabel(topFeature.value.feature || topFeature.value.name)}。`,
    `缺失字段数 ${missingFieldCount.value}；可运行任务 ${commands.value.length} 个。`,
  ],
}))
const qualityCards = computed(() => [
  { label: '样本数量', value: valueOrIssue(quality.value?.rows?.final_cleaned), icon: 'DataLine', gradient: 'var(--gradient-cyan)' },
  { label: '字段数量', value: valueOrIssue(quality.value?.schema?.column_count ?? quality.value?.columns?.total), icon: 'Document', gradient: 'var(--gradient-green)' },
  { label: '缺失字段数', value: missingFieldCount.value, icon: 'Warning', gradient: missingFieldCount.value ? 'var(--gradient-orange)' : 'var(--gradient-green)' },
  { label: '数据覆盖率', value: coverageText.value, icon: 'TrendCharts', gradient: 'var(--gradient-purple)' },
])
const periodText = computed(() => {
  const start = quality.value?.time_alignment?.min_timestamp || quality.value?.date_range?.start
  const end = quality.value?.time_alignment?.max_timestamp || quality.value?.date_range?.end
  return start && end ? `${String(start).slice(0, 10)} ~ ${String(end).slice(0, 10)}` : '数据缺失'
})
const coverageText = computed(() => {
  const coverage = Number(quality.value?.time_alignment?.target_hour_coverage)
  return Number.isFinite(coverage) ? `${(coverage * 100).toFixed(1)}%` : '数据缺失'
})
const integritySummary = computed(() => {
  const timeCoverage = Number(quality.value?.time_alignment?.target_hour_coverage)
  const hasTime = Number.isFinite(timeCoverage) && timeCoverage > 0
  const targetMissing = positiveMissingRows.value.filter(row => /target|pv_power|power/i.test(row.field)).length
  const timeMissing = positiveMissingRows.value.filter(row => /time|date|hour/i.test(row.field)).length
  return [
    { label: '字段缺失', value: `${missingFieldCount.value} 个`, ok: missingFieldCount.value === 0, hint: missingFieldCount.value === 0 ? '未发现缺失字段' : '存在需要处理的字段' },
    { label: '时间字段完整性', value: timeMissing === 0 && hasTime ? '完整' : '需检查', ok: timeMissing === 0 && hasTime, hint: `覆盖率 ${coverageText.value}` },
    { label: '目标字段完整性', value: targetMissing === 0 ? '完整' : '需检查', ok: targetMissing === 0, hint: targetMissing === 0 ? '目标功率字段可用' : '目标字段存在缺失' },
    { label: '特征字段完整性', value: missingFieldCount.value === 0 ? '完整' : '需检查', ok: missingFieldCount.value === 0, hint: `${valueOrIssue(quality.value?.schema?.column_count ?? quality.value?.columns?.total)} 个字段参与检查` },
  ]
})

function valueOrIssue(value) {
  return value === null || value === undefined || value === '' ? '数据缺失' : value
}

function fieldTypeLabel(field) {
  if (/time|hour|day|month|date/i.test(field)) return '时间字段'
  if (/flag|type|category|scenario/i.test(field)) return '类别字段'
  if (/kw|mw|eur|pct|deg|hpa|wm2|soc|humidity|temperature|speed|direction|albedo|water/i.test(field)) return '数值字段'
  return '数值 / 类别字段'
}

function missingSuggestion(field, count) {
  if (!count) return '无需处理'
  if (/pv_power|target/i.test(field)) return '优先剔除缺失目标记录，避免影响模型评估。'
  if (/time|date/i.test(field)) return '检查时间对齐规则，避免生成重复或断裂时间轴。'
  if (/flag|type|category|scenario/i.test(field)) return '使用“未知”类别或按业务规则补全。'
  return '按相邻时刻插值或使用训练集统计量补全。'
}

function commandDescription(commandId) {
  const map = {
    train_baseline: '读取特征数据并训练主预测模型。',
    compare_tabular: '对比多种预测模型的测试集误差表现。',
    run_inference: '使用当前展示模型刷新预测曲线。',
    run_dispatch: '基于预测功率生成储能充放电策略。',
    run_strategy: '比较不同调度策略的运行表现。',
    run_rolling: '按滚动窗口生成调度优化结果。',
    run_governance: '更新策略评分和运行建议。',
    run_sensitivity: '评估储能容量和功率配置对收益的影响。',
  }
  return map[commandId] || '执行系统任务。'
}

function normalizeTaskStatus(status) {
  const map = {
    pending: 'running',
    running: 'running',
    completed: 'success',
    success: 'success',
    failed: 'failed',
    timeout: 'timeout',
    idle: 'idle',
  }
  return map[status] || 'unknown'
}

function taskStatusLabel(status) {
  const map = {
    idle: '未运行',
    running: '运行中',
    success: '成功',
    failed: '失败',
    timeout: '超时',
    unknown: '状态未知',
  }
  return map[status] || '状态未知'
}

function isTaskRunning(commandId) {
  return taskStates.value[commandId]?.status === 'running'
}

function isSameTypeRunning(command) {
  return Object.values(taskStates.value).some(state => state.status === 'running' && state.type === command.type)
}

function taskButtonText(command) {
  const state = taskStates.value[command.command_id]
  if (state?.status === 'running') return '运行中...'
  if (state?.status === 'success') return '重新运行'
  if (['failed', 'timeout', 'unknown'].includes(state?.status)) return '重试'
  return command.label
}

function setTaskState(commandId, patch) {
  taskStates.value = {
    ...taskStates.value,
    [commandId]: {
      commandId,
      type: taskType(commandId),
      status: 'idle',
      message: '任务尚未运行。',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      ...(taskStates.value[commandId] || {}),
      ...patch,
      updatedAt: Date.now(),
    },
  }
}

function formatTimestamp(value) {
  if (!value) return '-'
  const ms = Number(value) < 10000000000 ? Number(value) * 1000 : Number(value)
  const date = new Date(ms)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function formatDuration(task) {
  if (!task?.startedAt) return '-'
  const end = ['success', 'failed', 'timeout', 'unknown'].includes(task.status) ? (task.finishedAt || task.updatedAt) : Date.now()
  return `${Math.max(0, (end - task.startedAt) / 1000).toFixed(1)}s`
}

function formatTaskDuration(task) {
  if (!task.started_at) return '-'
  const end = task.finished_at || task.updatedAt || Date.now() / 1000
  return `${Math.max(0, Number(end) - Number(task.started_at)).toFixed(1)}s`
}

function errorDetailFromStatus(status) {
  return status.stderr_tail || status.stdout_tail || status.detail || ''
}

function goTaskResult(commandId) {
  const map = {
    train_baseline: '/models',
    compare_tabular: '/models',
    run_inference: '/inspect',
    run_dispatch: '/dispatch',
    run_strategy: '/dispatch',
    run_rolling: '/dispatch',
    run_governance: '/dispatch',
    run_sensitivity: '/dispatch',
  }
  router.push(map[commandId] || '/data')
}

async function loadTasks() {
  try {
    taskHistory.value = await fetchTasks()
  } catch {
    taskHistory.value = []
  }
}

function stopPoll(taskId) {
  const poll = taskPolls.get(taskId)
  if (poll) clearInterval(poll)
  taskPolls.delete(taskId)
}

async function submitTask(cmdId) {
  activeTaskKey.value = cmdId
  const startedAt = Date.now()
  setTaskState(cmdId, { status: 'running', startedAt, createdAt: startedAt, message: `${taskLabel(cmdId)}正在运行。` })
  try {
    const submitted = await submitTaskCommand(cmdId)
    const taskId = submitted?.task_id
    if (!taskId) {
      setTaskState(cmdId, { status: 'failed', finishedAt: Date.now(), message: '任务创建失败：后端未返回任务编号。' })
      return
    }

    setTaskState(cmdId, { taskId, status: 'running', message: `${taskLabel(cmdId)}正在运行。` })
    const poll = setInterval(async () => {
      const current = taskStates.value[cmdId]
      if (current?.startedAt && Date.now() - current.startedAt > TASK_TIMEOUT_MS) {
        stopPoll(taskId)
        setTaskState(cmdId, { status: 'timeout', finishedAt: Date.now(), message: `${taskLabel(cmdId)}超时，请检查后端任务日志。` })
        loadTasks()
        return
      }
      try {
        const status = await fetchTaskStatus(taskId)
        const uiStatus = normalizeTaskStatus(status.status)
        if (uiStatus === 'running') {
          setTaskState(cmdId, { status: 'running', startedAt: status.started_at ? status.started_at * 1000 : current.startedAt, message: `${taskLabel(cmdId)}正在运行。` })
          return
        }
        stopPoll(taskId)
        setTaskState(cmdId, {
          status: uiStatus,
          finishedAt: status.finished_at ? status.finished_at * 1000 : Date.now(),
          message: uiStatus === 'success' ? `${taskLabel(cmdId)}已完成，结果已更新。` : `${taskLabel(cmdId)}执行失败。`,
          errorDetail: uiStatus === 'success' ? '' : errorDetailFromStatus(status),
        })
        loadTasks()
      } catch (e) {
        stopPoll(taskId)
        setTaskState(cmdId, {
          status: 'unknown',
          finishedAt: Date.now(),
          message: '任务状态无法确认，请稍后刷新最近任务。',
          errorDetail: e.normalized?.message || normalizeApiError(e).message,
        })
      }
    }, POLL_INTERVAL_MS)
    taskPolls.set(taskId, poll)
  } catch (e) {
    setTaskState(cmdId, {
      status: 'failed',
      finishedAt: Date.now(),
      message: '任务创建失败，请检查权限或后端服务状态。',
      errorDetail: e.normalized?.message || normalizeApiError(e).message,
    })
  }
}

async function loadDataExplorer() {
  loading.value = true
  error.value = null
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
.section-help,
.table-hint { color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.section-help { margin-bottom: var(--space-md); }
.integrity-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: var(--space-md); }
.integrity-item { background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-sm); padding: 12px; }
.integrity-item span,
.integrity-item small { color: var(--text-secondary); display: block; font-size: 12px; }
.integrity-item strong { display: block; font-size: 18px; margin: 4px 0; white-space: nowrap; }
.positive { color: var(--accent-green) !important; }
.negative { color: var(--accent-red) !important; }
.task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.task-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-md); padding: 12px 14px; }
.task-copy { display: flex; flex-direction: column; min-width: 0; }
.task-copy strong { color: var(--text-primary); font-size: 13px; }
.task-copy span { color: var(--text-secondary); font-size: 11px; overflow-wrap: anywhere; }
.task-feedback { border-color: rgba(0, 212, 255, 0.24); margin-top: var(--space-md); padding: var(--space-md); }
.task-feedback.success { border-color: rgba(0, 245, 160, 0.28); }
.task-feedback.failed,
.task-feedback.timeout,
.task-feedback.unknown { border-color: rgba(255, 82, 82, 0.32); }
.task-feedback-header,
.task-meta { align-items: center; display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.task-feedback-header strong { color: var(--text-primary); font-size: 13px; }
.task-feedback-header span,
.task-meta span { color: var(--text-secondary); font-size: 12px; }
.task-log-collapse { margin-top: 10px; }
.task-log-collapse pre { color: var(--text-secondary); font-size: 11px; overflow-x: auto; white-space: pre-wrap; }
.task-history { margin-top: var(--space-lg); border-top: 1px solid var(--border-glass); padding-top: var(--space-md); }
.task-history h4 { color: var(--text-secondary); font-size: 13px; margin-bottom: 8px; }
.task-history strong.success { color: var(--accent-green); }
.task-history strong.failed,
.task-history strong.timeout,
.task-history strong.unknown { color: var(--accent-red); }
.task-history strong.running { color: var(--accent-cyan); }

@media (max-width: 767px) {
  .feat-chart { height: 340px; }
  .task-grid { grid-template-columns: 1fr; }
  .task-item,
  .task-feedback-header,
  .task-meta { align-items: flex-start; flex-direction: column; }
}
</style>
