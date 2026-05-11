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

      <MetricGrid :items="qualityCards" min-width="240px" />

      <div class="explorer-tabs">
        <el-radio-group v-model="explorerMode" size="small">
          <el-radio-button value="forecast">预测特征贡献</el-radio-button>
          <el-radio-button value="dispatch">调度收益贡献</el-radio-button>
        </el-radio-group>
      </div>

      <template v-if="explorerMode === 'forecast'">
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

      <ChartCard title="预测特征贡献 Top20">
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

      <template v-else>
        <ChartCard title="调度收益影响程度">
          <p class="section-help">该图基于储能配置敏感性与收益情景结果进行相对影响分析，用于辅助判断容量、功率、约束和经济条件对仿真收益的影响。</p>
          <PageState
            v-if="comparisonLoading"
            type="loading"
            title="正在加载调度收益贡献"
            message="正在从调度展示与配置敏感性接口读取情景指标。"
          />
          <PageState
            v-else-if="comparisonError"
            type="error"
            title="调度收益贡献加载失败"
            :message="comparisonError.message"
            retryable
            @retry="loadComparisonData"
          />
          <PageState
            v-else-if="!dispatchImportanceRows.length"
            type="empty"
            title="暂无调度收益贡献"
            message="当前缺少储能调度敏感性结果，无法生成调度收益影响程度。"
            retryable
            @retry="loadComparisonData"
          />
          <template v-else>
            <v-chart class="feat-chart" :option="dispatchImportanceOption" theme="dark-tech" autoresize />
            <el-table :data="dispatchImportanceRows" stripe size="small" max-height="360" style="width: 100%">
              <el-table-column prop="label" label="影响因素" min-width="160" />
              <el-table-column prop="score" label="相对影响程度" width="130" sortable>
                <template #default="{ row }">{{ row.score.toFixed(1) }}</template>
              </el-table-column>
              <el-table-column prop="direction" label="方向说明" min-width="160" show-overflow-tooltip />
              <el-table-column prop="basis" label="计算依据" min-width="260" show-overflow-tooltip />
            </el-table>
          </template>
        </ChartCard>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageSection from '../components/PageSection.vue'
import PageState from '../components/PageState.vue'
import { buildFeatureImportanceOption, featureImportanceScore } from '../charts/dataExplorerCharts'
import { fetchDataExplorerBundle, fetchTasks, fetchTaskStatus, submitTaskCommand } from '../services/dataExplorerService'
import { fetchSensitivityMetrics } from '../services/governanceService'
import { fetchShowcaseScenarios } from '../services/dispatchService'
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
const explorerMode = ref('forecast')
const comparisonLoading = ref(false)
const comparisonError = ref(null)
const showcaseScenarios = ref([])
const sensitivityMetrics = ref([])

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

const comparisonRows = computed(() => {
  const scenarios = [...showcaseScenarios.value]
  const maxNet = Math.max(...scenarios.map(s => Math.abs(Number(s.net_incremental_revenue_eur) || 0)), 1)
  return scenarios
    .map(row => {
      const net = Number(row.net_incremental_revenue_eur) || 0
      const soh = Number(row.soh_end)
      const constraintsOk = row.constraints_passed !== false
      const score = clamp(Math.round((net / maxNet) * 50 + 50), 0, 100)
      const recommended = net >= 0 && constraintsOk
      const reasons = []
      if (net < 0) reasons.push(`净增量收益为负（${formatComparisonCurrency(net)}）`)
      if (!constraintsOk) reasons.push('调度约束未通过')
      if (!Number.isFinite(soh) || soh < 0.6) reasons.push(`SOC 健康度过低（${formatPercent(soh)}）`)
      return { ...row, score, recommended, reason: reasons.join('；') || '' }
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 20)
})

const dispatchImportanceRows = computed(() => buildDispatchImportanceRows(sensitivityMetrics.value, showcaseScenarios.value))
const dispatchImportanceOption = computed(() => buildDispatchImportanceOption(dispatchImportanceRows.value))

function buildDispatchImportanceRows(sensitivityRows, scenarioRows) {
  const rows = Array.isArray(sensitivityRows) ? sensitivityRows : []
  const factors = [
    groupedRangeFactor(rows, 'capacity_multiplier', '容量倍率', '按容量倍率分组比较平均增量收益差异'),
    groupedRangeFactor(rows, 'power_multiplier', '功率倍率', '按功率倍率分组比较平均增量收益差异'),
    groupedRangeFactor(rows, 'objective_preset', '目标组合', '按目标组合分组比较平均增量收益差异'),
    numericCorrelationFactor(rows, 'cycle_equivalent_count', '循环强度', '按等效循环次数与增量收益的相关关系估计'),
    numericCorrelationFactor(rows, 'total_shortfall_kwh', '短缺约束', '按短缺电量与增量收益的相关关系估计'),
    numericCorrelationFactor(rows, 'total_curtailed_kwh', '弃光影响', '按弃光电量与增量收益的相关关系估计'),
    numericCorrelationFactor(rows, 'soc_edge_touch_ratio', 'SOC 贴边影响', '按 SOC 贴边比例与增量收益的相关关系估计'),
    scenarioRangeFactor(scenarioRows),
  ].filter(Boolean)
  const maxScore = Math.max(...factors.map(row => row.rawScore), 0)
  return factors
    .map(row => ({ ...row, score: maxScore > 0 ? (row.rawScore / maxScore) * 100 : 0 }))
    .filter(row => row.score > 0)
    .sort((a, b) => b.score - a.score)
}

function groupedRangeFactor(rows, field, label, basis) {
  const groups = new Map()
  for (const row of rows) {
    const key = row[field]
    const revenue = Number(row.incremental_revenue_eur)
    if (key === undefined || key === null || key === '' || !Number.isFinite(revenue)) continue
    const group = groups.get(String(key)) || []
    group.push(revenue)
    groups.set(String(key), group)
  }
  if (groups.size < 2) return null
  const means = [...groups.entries()].map(([key, values]) => ({
    key,
    mean: values.reduce((sum, value) => sum + value, 0) / values.length,
  }))
  const best = [...means].sort((a, b) => b.mean - a.mean)[0]
  const worst = [...means].sort((a, b) => a.mean - b.mean)[0]
  return {
    factor: field,
    label,
    rawScore: Math.abs(best.mean - worst.mean),
    direction: `${best.key} 组平均收益较高`,
    basis,
  }
}

function numericCorrelationFactor(rows, field, label, basis) {
  const pairs = rows
    .map(row => ({ x: Number(row[field]), y: Number(row.incremental_revenue_eur) }))
    .filter(pair => Number.isFinite(pair.x) && Number.isFinite(pair.y))
  if (pairs.length < 3) return null
  const xs = pairs.map(pair => pair.x)
  const ys = pairs.map(pair => pair.y)
  const meanX = xs.reduce((sum, value) => sum + value, 0) / xs.length
  const meanY = ys.reduce((sum, value) => sum + value, 0) / ys.length
  const cov = pairs.reduce((sum, pair) => sum + (pair.x - meanX) * (pair.y - meanY), 0)
  const sx = Math.sqrt(xs.reduce((sum, value) => sum + (value - meanX) ** 2, 0))
  const sy = Math.sqrt(ys.reduce((sum, value) => sum + (value - meanY) ** 2, 0))
  if (!sx || !sy) return null
  const corr = cov / (sx * sy)
  const revenueRange = Math.max(...ys) - Math.min(...ys)
  return {
    factor: field,
    label,
    rawScore: Math.abs(corr) * Math.abs(revenueRange),
    direction: corr >= 0 ? '数值升高时收益倾向升高' : '数值升高时收益倾向降低',
    basis,
  }
}

function scenarioRangeFactor(rows) {
  const values = (Array.isArray(rows) ? rows : [])
    .map(row => Number(row.net_incremental_revenue_eur))
    .filter(Number.isFinite)
  if (values.length < 2) return null
  return {
    factor: 'scenario_economic_condition',
    label: '经济情景条件',
    rawScore: Math.max(...values) - Math.min(...values),
    direction: '不同收益情景下净增量收益存在差异',
    basis: '按多收益情景净增量收益区间估计',
  }
}

function buildDispatchImportanceOption(rows) {
  if (!rows.length) return {}
  const sorted = [...rows].sort((a, b) => a.score - b.score)
  return {
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const item = params?.[0]
        const row = sorted[item?.dataIndex]
        if (!row) return ''
        return [`<strong>${row.label}</strong>`, `相对影响程度：${row.score.toFixed(1)}`, row.direction, row.basis].join('<br/>')
      },
    },
    grid: { left: 150, right: 42, top: 18, bottom: 36 },
    xAxis: {
      type: 'value',
      name: '相对影响程度',
      max: 100,
      nameTextStyle: { color: 'rgba(255,255,255,0.74)' },
      axisLabel: { color: 'rgba(255,255,255,0.74)' },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.10)' } },
    },
    yAxis: {
      type: 'category',
      data: sorted.map(row => row.label),
      axisLabel: { color: 'rgba(255,255,255,0.82)' },
    },
    series: [
      {
        type: 'bar',
        data: sorted.map((row, index) => ({
          value: Number(row.score.toFixed(1)),
          itemStyle: { color: `hsl(${170 + index * 10}, 72%, 54%)` },
        })),
        barMaxWidth: 18,
      },
    ],
  }
}

function scenarioTypeLabel(type) {
  const map = { baseline: '基准纯套利', price_volatility: '价格波动增强', capacity_revenue: '容量价值叠加', cost_improvement: '退化成本改善', pure_arbitrage_best: '最优纯套利', degradation_aware: '退化约束主动循环', aggressive_baseline: '激进策略对照' }
  return map[type] || type || '—'
}

function formatComparisonCurrency(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${n.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })} EUR` : '—'
}

function formatPercent(value) {
  const n = Number(value)
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—'
}

function clamp(value, min, max) { return Math.min(Math.max(Number(value), min), max) }

async function loadComparisonData() {
  comparisonLoading.value = true
  comparisonError.value = null
  try {
    const [showcase, sensitivity] = await Promise.all([
      fetchShowcaseScenarios().catch(() => []),
      fetchSensitivityMetrics().catch(() => []),
    ])
    showcaseScenarios.value = Array.isArray(showcase) ? showcase : []
    sensitivityMetrics.value = Array.isArray(sensitivity) ? sensitivity : []
  } catch (e) {
    comparisonError.value = e.normalized || normalizeApiError(e)
  } finally {
    comparisonLoading.value = false
  }
}

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

watch(explorerMode, (mode) => {
  if (mode === 'dispatch' && !showcaseScenarios.value.length && !sensitivityMetrics.value.length) {
    loadComparisonData()
  }
})

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
.explorer-tabs { display: flex; align-items: center; }
.explorer-tabs :deep(.el-radio-button__inner) { font-size: 13px; }
.scheme-badge { background: rgba(255,255,255,0.08); border-radius: var(--radius-full); color: var(--text-secondary); display: inline-flex; font-size: 11px; font-weight: 700; padding: 2px 8px; white-space: nowrap; }
.scheme-badge.recommended { background: rgba(0, 255, 136, 0.14); color: var(--accent-green); }
.scheme-badge.not-recommended { background: rgba(255, 82, 82, 0.12); color: var(--accent-red); }

@media (max-width: 767px) {
  .feat-chart { height: 340px; }
  .task-grid { grid-template-columns: 1fr; }
  .task-item,
  .task-feedback-header,
  .task-meta { align-items: flex-start; flex-direction: column; }
}
</style>
