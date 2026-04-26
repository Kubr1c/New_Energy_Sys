<template>
  <div class="app-root dark">
    <!-- Sidebar Navigation -->
    <aside v-if="isAuthenticated && $route.name !== 'Login'" class="app-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="sidebar-logo" @click="$router.push('/')">
        <span class="logo-icon">⚡</span>
        <span v-if="!sidebarCollapsed" class="logo-text">NES Platform</span>
      </div>

      <nav class="sidebar-nav">
        <router-link v-for="item in navItems" :key="item.path" :to="item.path"
          class="nav-item" :class="{ active: $route.path === item.path }">
          <el-icon :size="20"><component :is="item.icon" /></el-icon>
          <span v-if="!sidebarCollapsed" class="nav-label">{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <button class="collapse-btn" @click="sidebarCollapsed = !sidebarCollapsed">
          <el-icon :size="16"><Fold v-if="!sidebarCollapsed" /><Expand v-else /></el-icon>
        </button>
      </div>
    </aside>

    <!-- Main Content -->
    <div class="app-main" :class="{ 'no-sidebar': !isAuthenticated || $route.name === 'Login', 'sidebar-collapsed': sidebarCollapsed }">
      <!-- Top Bar -->
      <header v-if="isAuthenticated && $route.name !== 'Login'" class="app-header glass-panel">
        <div class="header-left">
          <h2 class="page-title">{{ currentPageTitle }}</h2>
        </div>
        <div class="header-right">
          <span class="header-clock">{{ currentTime }}</span>
          <div class="header-user" @click="handleLogout">
            <el-icon><User /></el-icon>
            <span>{{ currentUser?.display_name || 'User' }}</span>
            <el-icon class="logout-icon"><SwitchButton /></el-icon>
          </div>
        </div>
      </header>

      <!-- Page Content -->
      <main class="app-content">
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

const sidebarCollapsed = ref(false)
const currentTime = ref('')
let timer = null

const navItems = [
  { path: '/', label: '系统总览 Overview', icon: 'DataAnalysis' },
  { path: '/models', label: '模型对比 Models', icon: 'TrendCharts' },
  { path: '/dispatch', label: '调度仿真 Dispatch', icon: 'Setting' },
  { path: '/governance', label: '策略治理 Governance', icon: 'Histogram' },
  { path: '/data', label: '数据探索 Data', icon: 'DataLine' },
  { path: '/reports', label: '实验报告 Reports', icon: 'Document' },
]

const isAuthenticated = computed(() => !!localStorage.getItem('nes_token'))
const currentUser = computed(() => {
  try { return JSON.parse(localStorage.getItem('nes_user') || 'null') } catch { return null }
})
const currentPageTitle = computed(() => {
  const matched = navItems.find(n => n.path === route.path)
  return matched?.label || ''
})

function updateTime() {
  const now = new Date()
  currentTime.value = now.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function handleLogout() {
  localStorage.removeItem('nes_token')
  localStorage.removeItem('nes_user')
  router.push('/login')
}

onMounted(() => {
  updateTime()
  timer = setInterval(updateTime, 1000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.app-root {
  display: flex;
  min-height: 100vh;
  background: var(--bg-primary);
}

/* ---- Sidebar ---- */
.app-sidebar {
  width: 240px;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-glass);
  display: flex;
  flex-direction: column;
  transition: width var(--duration-normal) var(--ease-default);
  position: fixed;
  top: 0; left: 0; bottom: 0;
  z-index: 100;
}
.app-sidebar.collapsed { width: 64px; }

.sidebar-logo {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-glass);
  cursor: pointer;
  overflow: hidden;
}
.logo-icon {
  font-size: 24px;
  min-width: 24px;
  filter: drop-shadow(0 0 8px rgba(0, 212, 255, 0.6));
}
.logo-text {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-cyan);
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.sidebar-nav {
  flex: 1;
  padding: var(--space-md) var(--space-sm);
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  transition: all var(--duration-fast) var(--ease-default);
  text-decoration: none;
  position: relative;
  overflow: hidden;
  white-space: nowrap;
}
.nav-item:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.nav-item.active {
  color: var(--accent-cyan);
  background: rgba(0, 212, 255, 0.08);
}
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0; top: 20%; bottom: 20%;
  width: 3px;
  background: var(--accent-cyan);
  border-radius: 0 2px 2px 0;
  box-shadow: var(--shadow-glow-cyan);
}
.nav-label { font-size: 13px; font-weight: 500; }

.sidebar-footer {
  padding: var(--space-md);
  border-top: 1px solid var(--border-glass);
}
.collapse-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border: none;
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-default);
}
.collapse-btn:hover { background: rgba(255,255,255,0.1); color: var(--text-primary); }

/* ---- Main ---- */
.app-main {
  flex: 1;
  margin-left: 240px;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  transition: margin-left var(--duration-normal) var(--ease-default);
}
.app-main.no-sidebar { margin-left: 0; }
.app-main.sidebar-collapsed { margin-left: 64px; }

/* ---- Header ---- */
.app-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-xl);
  border-bottom: 1px solid var(--border-glass);
  border-radius: 0;
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(10, 14, 39, 0.85) !important;
  backdrop-filter: blur(16px);
}
.page-title { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.header-right { display: flex; align-items: center; gap: var(--space-lg); }
.header-clock {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-input);
  padding: 4px 12px;
  border-radius: var(--radius-full);
}
.header-user {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px 12px;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-default);
  font-size: 13px;
}
.header-user:hover { background: var(--bg-hover); color: var(--accent-red); }
.logout-icon { font-size: 14px; }

/* ---- Content ---- */
.app-content {
  flex: 1;
  padding: var(--space-xl);
  overflow-y: auto;
}

@media (max-width: 1199px) {
  .app-sidebar { width: 72px; }
  .app-sidebar:not(.collapsed) .logo-text,
  .app-sidebar:not(.collapsed) .nav-label { display: none; }
  .app-main,
  .app-main.sidebar-collapsed { margin-left: 72px; }
  .app-content { padding: var(--space-lg); }
}

@media (max-width: 767px) {
  .app-root { flex-direction: column; }
  .app-sidebar,
  .app-sidebar.collapsed {
    position: sticky;
    top: 0;
    width: 100%;
    height: auto;
    flex-direction: row;
    align-items: center;
    border-right: 0;
    border-bottom: 1px solid var(--border-glass);
  }
  .sidebar-logo { width: 56px; height: 56px; padding: 0 16px; border-bottom: 0; }
  .logo-text, .nav-label, .sidebar-footer { display: none; }
  .sidebar-nav { flex-direction: row; padding: 8px; overflow-x: auto; }
  .nav-item { width: 40px; height: 40px; justify-content: center; padding: 0; flex: 0 0 40px; }
  .nav-item.active::before { left: 20%; right: 20%; top: auto; bottom: 0; width: auto; height: 3px; border-radius: 2px 2px 0 0; }
  .app-main,
  .app-main.sidebar-collapsed { margin-left: 0; }
  .app-header { height: auto; min-height: 56px; padding: 10px 16px; gap: 12px; align-items: flex-start; }
  .header-right { gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .header-clock { display: none; }
  .page-title { font-size: 14px; }
  .app-content { padding: 16px; }
}
</style>
