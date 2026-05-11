<template>
  <section class="insight-summary glass-panel animate-fade-in-up" :class="toneClass">
    <div class="insight-icon">
      <el-icon :size="22"><DataAnalysis /></el-icon>
    </div>
    <div class="insight-content">
      <div class="insight-kicker">核心结论</div>
      <h3>{{ title }}</h3>
      <ul v-if="items.length" class="insight-list">
        <li v-for="item in items" :key="item">{{ item }}</li>
      </ul>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { DataAnalysis } from '@element-plus/icons-vue'

const props = defineProps({
  title: {
    type: String,
    required: true,
  },
  items: {
    type: Array,
    default: () => [],
  },
  tone: {
    type: String,
    default: 'neutral',
  },
})

const toneClass = computed(() => `tone-${props.tone}`)
</script>

<style>
.insight-summary {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: var(--space-md);
  align-items: flex-start;
  border-left: 4px solid var(--accent-cyan);
  padding: var(--space-lg);
}

.insight-summary.tone-positive { border-left-color: var(--accent-green); }
.insight-summary.tone-warning { border-left-color: var(--accent-orange); }
.insight-summary.tone-negative { border-left-color: var(--accent-red); }

.insight-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  background: rgba(0, 212, 255, 0.14);
  color: var(--accent-cyan);
}

.tone-positive .insight-icon {
  background: rgba(0, 245, 160, 0.14);
  color: var(--accent-green);
}

.tone-warning .insight-icon {
  background: rgba(255, 167, 38, 0.14);
  color: var(--accent-orange);
}

.tone-negative .insight-icon {
  background: rgba(255, 82, 82, 0.14);
  color: var(--accent-red);
}

.insight-content { min-width: 0; }

.insight-kicker {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  margin-bottom: 5px;
}

.insight-summary h3 {
  color: var(--text-primary);
  font-size: 17px;
  font-weight: 700;
  line-height: 1.45;
}

.insight-list {
  display: grid;
  gap: 6px;
  margin-top: 10px;
  padding-left: 18px;
}

.insight-list li {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.55;
}

@media (max-width: 767px) {
  .insight-summary {
    grid-template-columns: 1fr;
    padding: var(--space-md);
  }
}
</style>
