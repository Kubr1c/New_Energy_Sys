<template>
  <div class="report-viewer">
    <InsightSummary :title="reportInsight.title" :items="reportInsight.items" :tone="reportInsight.tone" />

    <div class="rv-layout">
      <aside class="glass-card stage-list">
        <div class="stage-header">
          <h3>实验阶段</h3>
          <span>{{ stages.length }} 份报告</span>
        </div>
        <button
          v-for="stage in stages"
          :key="stage.stage_id"
          type="button"
          class="stage-btn"
          :class="{ active: selected === stage.stage_id }"
          :title="stage.name"
          @click="selectStage(stage.stage_id)"
        >
          <span class="stage-badge">{{ stage.stage_id.replace('stage', 'S') }}</span>
          <span class="stage-label">{{ stageLabel(stage.name) }}</span>
          <span class="stage-format">{{ stage.has_md ? 'MD' : 'JSON' }}</span>
        </button>
      </aside>

      <section class="glass-card report-content">
        <div v-if="loading" class="report-loading">
          <el-icon class="spin" :size="24"><Loading /></el-icon>
          <span>正在加载报告...</span>
        </div>
        <PageState
          v-else-if="error && !mdContent"
          type="error"
          title="报告加载失败"
          :message="error.message"
          retryable
          @retry="selected && selectStage(selected)"
        />
        <div v-else-if="mdContent" class="markdown-body" v-html="renderedMd"></div>
        <PageState
          v-else
          type="empty"
          title="请选择实验阶段"
          :message="`左侧共有 ${stages.length} 个可用阶段报告，进入页面会默认打开第一个可用报告。`"
        />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import InsightSummary from '../components/InsightSummary.vue'
import PageState from '../components/PageState.vue'
import { fetchReportMarkdown, fetchReportStages } from '../services/reportService'
import { normalizeApiError } from '../utils/api'
import { renderMarkdown } from '../utils/markdown'

const stages = ref([])
const selected = ref('')
const mdContent = ref('')
const loading = ref(false)
const error = ref(null)

const renderedMd = computed(() => renderMarkdown(mdContent.value))
const currentStage = computed(() => stages.value.find(stage => stage.stage_id === selected.value) || {})
const reportInsight = computed(() => ({
  title: stages.value.length
    ? `当前共有 ${stages.value.length} 份阶段报告，默认打开 ${stageLabel(currentStage.value.name || currentStage.value.stage_id || stages.value[0]?.stage_id)}。`
    : '当前没有可用阶段报告，请先生成实验报告产物。',
  tone: stages.value.length ? 'positive' : 'warning',
  items: [
    `当前选中阶段：${selected.value || '未选择'}。`,
    `当前报告格式：${currentStage.value.has_md ? 'Markdown' : currentStage.value.stage_id ? 'JSON' : '数据缺失'}。`,
    error.value ? `最近一次加载提示：${error.value.message}` : '报告内容通过本地 Markdown 清洗后展示。',
  ],
}))

function stageLabel(name) {
  return String(name || '').replace(/_/g, ' ')
}

async function selectStage(stageId) {
  selected.value = stageId
  loading.value = true
  mdContent.value = ''
  error.value = null
  try {
    mdContent.value = await fetchReportMarkdown(stageId)
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    stages.value = await fetchReportStages()
    if (stages.value.length) {
      await selectStage(stages.value[0].stage_id)
    }
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  }
})
</script>

<style scoped>
.report-viewer { display: flex; flex-direction: column; gap: var(--space-lg); height: calc(100vh - 112px); min-height: 560px; }
.rv-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: var(--space-lg);
  min-height: 0;
  flex: 1;
}
.stage-list {
  min-width: 0;
  overflow-y: auto;
  padding: var(--space-lg);
}
.stage-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: var(--space-md);
}
.stage-header h3 { color: var(--text-primary); font-size: 15px; font-weight: 700; }
.stage-header span { color: var(--text-secondary); font-size: 12px; }
.stage-btn {
  width: 100%;
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) 42px;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  margin-bottom: 5px;
  padding: 8px 10px;
  text-align: left;
}
.stage-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.stage-btn.active { background: rgba(0, 212, 255, 0.12); color: var(--text-primary); }
.stage-badge { color: var(--accent-cyan); font-family: var(--font-mono); font-size: 11px; font-weight: 800; }
.stage-label {
  font-size: 12px;
  line-height: 1.35;
  min-width: 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.stage-format { color: var(--text-tertiary); font-size: 10px; text-align: right; }
.report-content { overflow-y: auto; overflow-x: hidden; padding: var(--space-xl); }
.report-loading {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.markdown-body { color: var(--text-primary); line-height: 1.8; font-size: 14px; overflow-wrap: anywhere; }
.markdown-body :deep(h1) { color: var(--accent-cyan); font-size: 22px; font-weight: 700; margin: 8px 0 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border-glass); }
.markdown-body :deep(h2) { font-size: 18px; font-weight: 700; margin: 20px 0 10px; }
.markdown-body :deep(h3) { font-size: 15px; font-weight: 700; margin: 16px 0 8px; }
.markdown-body :deep(p) { margin-bottom: 12px; }
.markdown-body :deep(code) { background: var(--bg-input); border-radius: 4px; color: var(--accent-cyan); font-family: var(--font-mono); font-size: 13px; padding: 2px 6px; }
.markdown-body :deep(pre) { background: var(--bg-secondary); border: 1px solid var(--border-glass); border-radius: var(--radius-md); margin: 12px 0; max-width: 100%; overflow-x: auto; padding: 16px; }
.markdown-body :deep(table) { width: 100%; border-collapse: collapse; display: block; overflow-x: auto; margin: 12px 0; }
.markdown-body :deep(th),
.markdown-body :deep(td) { border-bottom: 1px solid var(--border-glass); padding: 8px 12px; }
.markdown-body :deep(th) { color: var(--text-secondary); font-size: 12px; text-align: left; }
.markdown-body :deep(td) { font-size: 13px; }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--accent-cyan); color: var(--text-secondary); margin: 12px 0; padding-left: 16px; }

@media (max-width: 1199px) {
  .rv-layout { grid-template-columns: 260px minmax(0, 1fr); }
}

@media (max-width: 767px) {
  .report-viewer { height: auto; min-height: calc(100vh - 112px); }
  .rv-layout { grid-template-columns: 1fr; }
  .stage-list { max-height: 260px; }
  .report-content { padding: var(--space-lg); }
}
</style>
