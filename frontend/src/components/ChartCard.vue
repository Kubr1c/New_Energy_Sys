<template>
  <section class="chart-card glass-card animate-fade-in-up">
    <div class="chart-header">
      <h3>{{ title }}</h3>
      <div v-if="$slots.actions" class="chart-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="chart-body">
      <slot />
    </div>
  </section>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
})
</script>

<style scoped>
.chart-card {
  min-width: 0;
  padding: 0;
  overflow: hidden;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  min-height: 58px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-glass);
}

.chart-header h3 {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.chart-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-shrink: 0;
}

.chart-body {
  padding: 18px 20px 20px;
}

@media (max-width: 767px) {
  .chart-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
  }

  .chart-body {
    padding: 16px;
  }
}
</style>
