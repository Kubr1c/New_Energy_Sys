<template>
  <div class="metric-grid" :style="{ '--metric-min-width': minWidth }">
    <MetricCard
      v-for="(item, index) in items"
      :key="item.key || item.label"
      :class="'animate-delay-' + (index + 1)"
      :label="item.label"
      :value="item.value"
      :icon="item.icon"
      :icon-type="item.iconType"
      :gradient="item.gradient"
    />
  </div>
</template>

<script setup>
import MetricCard from './MetricCard.vue'

defineProps({
  items: {
    type: Array,
    required: true,
  },
  minWidth: {
    type: String,
    default: '220px',
  },
})
</script>

<style scoped>
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--metric-min-width)), 1fr));
  gap: var(--space-lg);
}

@media (max-width: 767px) {
  .metric-grid {
    gap: var(--space-md);
  }
}
</style>
