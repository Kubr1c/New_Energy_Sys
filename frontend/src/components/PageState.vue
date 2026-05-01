<template>
  <div class="page-state glass-panel" :class="type">
    <el-icon v-if="type === 'loading'" class="state-icon spin" :size="28"><Loading /></el-icon>
    <el-icon v-else-if="type === 'error'" class="state-icon" :size="28"><WarningFilled /></el-icon>
    <el-icon v-else class="state-icon" :size="28"><Document /></el-icon>

    <div class="state-copy">
      <h3>{{ title }}</h3>
      <p>{{ message }}</p>
      <button v-if="retryable" type="button" class="retry-btn" @click="$emit('retry')">
        重试 Retry
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  type: {
    type: String,
    default: 'empty',
  },
  title: {
    type: String,
    required: true,
  },
  message: {
    type: String,
    required: true,
  },
  retryable: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['retry'])
</script>

<style scoped>
.page-state {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: var(--space-xl);
  text-align: left;
  background: rgba(17, 22, 51, 0.78);
}
.state-icon {
  color: var(--accent-cyan);
  flex: 0 0 auto;
}
.page-state.error .state-icon { color: var(--accent-red); }
.state-copy { max-width: 520px; }
.state-copy h3 {
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 8px;
}
.state-copy p {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 14px;
}
.retry-btn {
  border: 1px solid var(--border-active);
  border-radius: var(--radius-sm);
  background: rgba(0, 212, 255, 0.1);
  color: var(--accent-cyan);
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  padding: 8px 14px;
}
.retry-btn:hover { background: rgba(0, 212, 255, 0.18); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (max-width: 767px) {
  .page-state {
    align-items: flex-start;
    flex-direction: column;
    min-height: 200px;
    padding: var(--space-lg);
  }
}
</style>
