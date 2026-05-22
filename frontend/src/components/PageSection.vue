<template>
  <section class="page-section glass-card animate-fade-in-up">
    <div v-if="title || $slots.actions" class="section-header">
      <h3 v-if="title">{{ title }}</h3>
      <div v-if="$slots.actions" class="section-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="section-body">
      <slot />
    </div>
  </section>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    default: '',
  },
})
</script>

<style scoped>
.page-section {
  min-width: 0;
  padding: 0;
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  min-height: 58px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-glass);
}

.section-header h3 {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.section-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-shrink: 0;
}

.section-body {
  padding: 18px 20px 20px;
}

@media (max-width: 767px) {
  .section-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
  }

  .section-body {
    padding: 16px;
  }
}
</style>
