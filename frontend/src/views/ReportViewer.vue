<template>
  <div class="report-viewer">
    <div class="rv-layout">
      <!-- Stage selector -->
      <div class="glass-card stage-list animate-fade-in-up">
        <h3>📄 实验阶段 Stages</h3>
        <div v-for="s in stages" :key="s.stage_id" class="stage-btn" :class="{ active: selected === s.stage_id }" @click="selectStage(s.stage_id)">
          <span class="stage-badge">{{ s.stage_id.replace('stage', 'S') }}</span>
          <span class="stage-label">{{ s.name }}</span>
        </div>
      </div>

      <!-- Report content -->
      <div class="glass-card report-content animate-fade-in-up animate-delay-2">
        <div v-if="loading" class="report-loading">
          <el-icon class="spin" :size="24"><Loading /></el-icon>
          <span>Loading report...</span>
        </div>
        <div v-else-if="mdContent" class="markdown-body" v-html="renderedMd"></div>
        <div v-else class="report-empty">
          <p>← 请选择一个实验阶段查看报告</p>
          <p>Select a stage from the left panel to view its report</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import api from '../utils/api'
import { normalizeApiError } from '../utils/api'
import { renderMarkdown } from '../utils/markdown'

const stages = ref([])
const selected = ref('')
const mdContent = ref('')
const loading = ref(false)
const error = ref(null)

const renderedMd = computed(() => renderMarkdown(mdContent.value))

async function selectStage(stageId) {
  selected.value = stageId
  loading.value = true
  mdContent.value = ''
  error.value = null
  try {
    const res = await api.get(`/api/reports/${stageId}/md`)
    mdContent.value = res.data.content || ''
  } catch (e) {
    const apiError = e.normalized || normalizeApiError(e)
    error.value = apiError
    mdContent.value = `> 报告加载失败\n\n${apiError.message}\n\nNo report available for ${stageId}.`
  } finally { loading.value = false }
}

onMounted(async () => {
  try {
    const res = await api.get('/api/reports/list')
    stages.value = res.data
  } catch (e) { error.value = e.normalized || normalizeApiError(e) }
})
</script>

<style scoped>
.report-viewer { height: calc(100vh - 120px); }
.rv-layout { display: grid; grid-template-columns: 260px 1fr; gap: var(--space-lg); height: 100%; }

.stage-list { padding: var(--space-lg); overflow-y: auto; overflow-x: hidden; }
.stage-list h3 { font-size: 14px; font-weight: 600; margin-bottom: var(--space-md); }
.stage-btn {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  border-radius: var(--radius-sm); cursor: pointer;
  transition: all var(--duration-fast) var(--ease-default); margin-bottom: 4px;
  min-width: 0;
}
.stage-btn:hover { background: var(--bg-hover); }
.stage-btn.active { background: rgba(0,212,255,0.1); }
.stage-badge { font-size: 11px; font-weight: 700; color: var(--accent-cyan); font-family: var(--font-mono); min-width: 28px; }
.stage-label {
  color: var(--text-secondary);
  font-size: 12px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stage-btn.active .stage-label { color: var(--text-primary); }

.report-content { padding: var(--space-xl); overflow-y: auto; overflow-x: hidden; }
.report-loading { display: flex; align-items: center; gap: 12px; color: var(--text-secondary); justify-content: center; padding: 60px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.report-empty { text-align: center; color: var(--text-tertiary); padding: 80px 20px; font-size: 14px; line-height: 2; }

/* Markdown styling */
.markdown-body { color: var(--text-primary); line-height: 1.8; font-size: 14px; overflow-wrap: anywhere; }
.markdown-body :deep(h1) { font-size: 22px; font-weight: 700; margin: 24px 0 12px; color: var(--accent-cyan); border-bottom: 1px solid var(--border-glass); padding-bottom: 8px; }
.markdown-body :deep(h2) { font-size: 18px; font-weight: 600; margin: 20px 0 10px; color: var(--text-primary); }
.markdown-body :deep(h3) { font-size: 15px; font-weight: 600; margin: 16px 0 8px; }
.markdown-body :deep(p) { margin-bottom: 12px; }
.markdown-body :deep(code) { background: var(--bg-input); padding: 2px 6px; border-radius: 4px; font-family: var(--font-mono); font-size: 13px; color: var(--accent-cyan); }
.markdown-body :deep(pre) { background: var(--bg-secondary); padding: 16px; border-radius: var(--radius-md); overflow-x: auto; max-width: 100%; margin: 12px 0; border: 1px solid var(--border-glass); }
.markdown-body :deep(pre code) { background: none; padding: 0; }
.markdown-body :deep(table) { width: 100%; max-width: 100%; border-collapse: collapse; margin: 12px 0; }
.markdown-body :deep(th) { background: rgba(255,255,255,0.04); padding: 8px 12px; text-align: left; font-size: 12px; color: var(--text-secondary); border-bottom: 1px solid var(--border-glass); }
.markdown-body :deep(td) { padding: 8px 12px; font-size: 13px; border-bottom: 1px solid var(--border-glass); }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--accent-cyan); padding-left: 16px; color: var(--text-secondary); margin: 12px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 24px; margin-bottom: 12px; }
.markdown-body :deep(li) { margin-bottom: 4px; }

@media (max-width: 1199px) {
  .rv-layout { grid-template-columns: 220px minmax(0, 1fr); }
  .report-content, .stage-list { min-width: 0; }
}

@media (max-width: 767px) {
  .report-viewer { height: auto; min-height: calc(100vh - 112px); }
  .rv-layout { grid-template-columns: 1fr; }
  .stage-list { max-height: 240px; }
  .report-content { padding: var(--space-lg); }
  .markdown-body :deep(table) { display: block; overflow-x: auto; }
  .markdown-body :deep(h1) { font-size: 20px; }
}
</style>
