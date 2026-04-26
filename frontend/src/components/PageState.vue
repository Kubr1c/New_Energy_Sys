<template>
  <div class="page-state" :class="type">
    <el-icon v-if="type === 'loading'" class="state-icon spin" :size="24"><Loading /></el-icon>
    <el-icon v-else-if="type === 'error'" class="state-icon" :size="24"><WarningFilled /></el-icon>
    <el-icon v-else class="state-icon" :size="24"><Document /></el-icon>

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
  min-height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: var(--space-xl);
  color: var(--text-secondary);
  text-align: left;
}
.state-icon {
  color: var(--accent-cyan);
  flex: 0 0 auto;
}
.page-state.error .state-icon { color: var(--accent-red); }
.state-copy { max-width: 420px; }
.state-copy h3 {
  margin-bottom: 6px;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
}
.state-copy p {
  margin-bottom: 14px;
  font-size: 13px;
  line-height: 1.6;
}
.retry-btn {
  border: 1px solid var(--border-active);
  border-radius: var(--radius-sm);
  background: rgba(0, 212, 255, 0.08);
  color: var(--accent-cyan);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
}
.retry-btn:hover { background: rgba(0, 212, 255, 0.14); }
.spin { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@media (max-width: 767px) {
  .page-state {
    align-items: flex-start;
    flex-direction: column;
    min-height: 180px;
    padding: var(--space-lg);
  }
}
</style>
