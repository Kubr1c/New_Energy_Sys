<template>
  <section class="chart-card glass-card animate-fade-in-up">
    <div class="chart-header">
      <h3>{{ title }}</h3>
      <div v-if="$slots.actions" class="chart-actions">
        <slot name="actions" />
      </div>
    </div>
    <slot />
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
  padding: var(--space-lg);
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  margin-bottom: var(--space-md);
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

@media (max-width: 767px) {
  .chart-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
}
</style>
