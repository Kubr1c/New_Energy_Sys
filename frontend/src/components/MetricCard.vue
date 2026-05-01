<template>
  <div class="metric-card glass-card animate-fade-in-up">
    <div v-if="icon" class="metric-icon" :style="{ background: gradient }">
      <el-icon v-if="iconType === 'component'" :size="22">
        <component :is="icon" />
      </el-icon>
      <span v-else>{{ icon }}</span>
    </div>
    <div class="metric-body">
      <div class="metric-value display-number">{{ value }}</div>
      <div class="metric-label">{{ label }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  label: {
    type: String,
    required: true,
  },
  value: {
    type: [String, Number],
    required: true,
  },
  icon: {
    type: String,
    default: '',
  },
  iconType: {
    type: String,
    default: 'component',
  },
  gradient: {
    type: String,
    default: 'var(--gradient-cyan)',
  },
})
</script>

<style scoped>
.metric-card {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  min-width: 0;
  padding: 20px 24px;
}

.metric-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-md);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 20px;
}

.metric-body {
  min-width: 0;
}

.metric-value {
  color: var(--text-primary);
  font-size: 22px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}

.metric-label {
  color: var(--text-secondary);
  font-size: 12px;
  margin-top: 2px;
}

@media (max-width: 767px) {
  .metric-card {
    padding: 16px;
  }
}
</style>
