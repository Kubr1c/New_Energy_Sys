<template>
  <div class="app-root dark">
    <aside v-if="showShell" class="app-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <button class="sidebar-logo" type="button" @click="$router.push('/')">
        <span class="logo-icon">光</span>
        <span v-if="!sidebarCollapsed" class="logo-text">新能源调度系统</span>
      </button>

      <nav class="sidebar-nav" aria-label="Primary navigation">
        <router-link v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item" :class="{ active: $route.path === item.path }">
          <el-icon :size="20"><component :is="item.icon" /></el-icon>
          <span v-if="!sidebarCollapsed" class="nav-label">{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <button class="collapse-btn" type="button" @click="sidebarCollapsed = !sidebarCollapsed">
          <el-icon :size="16"><Fold v-if="!sidebarCollapsed" /><Expand v-else /></el-icon>
        </button>
      </div>
    </aside>

    <div class="app-main" :class="{ 'no-sidebar': !showShell, 'sidebar-collapsed': sidebarCollapsed }">
      <header v-if="showShell" class="app-header glass-panel">
        <div class="header-left">
          <h2 class="page-title">{{ currentPageTitle }}</h2>
          <p class="page-subtitle">{{ currentPageSubtitle }}</p>
        </div>
        <div class="header-right">
          <span class="header-clock">{{ currentTime }}</span>
          <button class="header-user" type="button" @click="handleLogout" title="退出登录">
            <el-icon><User /></el-icon>
            <span>{{ displayUserName }}</span>
            <el-icon class="logout-icon"><SwitchButton /></el-icon>
          </button>
        </div>
      </header>

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
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearAuthSession, currentUser, isAuthenticated, restoreAuthSession } from './stores/authState'

const router = useRouter()
const route = useRoute()
const sidebarCollapsed = ref(false)
const currentTime = ref('')
let timer = null

const navItems = [
  { path: '/', label: '系统总览', subtitle: '净增量收益、多情景展示与完整管线', icon: 'DataAnalysis' },
  { path: '/dispatch', label: '储能调度', subtitle: '多收益情景量化展示与退化约束策略评价', icon: 'Setting' },
  { path: '/models', label: '预测输入', subtitle: '光伏功率预测质量与调度输入说明', icon: 'TrendCharts' },
  { path: '/governance', label: '配置与退化', subtitle: '容量功率敏感性、退化成本与配置优选', icon: 'Histogram' },
  { path: '/data', label: '数据管理', subtitle: '数据质量、特征贡献与运行任务', icon: 'DataLine' },
  { path: '/reports', label: '项目报告', subtitle: '当前结论、阶段报告与论文材料入口', icon: 'Document' },
  { path: '/inspect', label: '预测验收', subtitle: '预测结果多日对比与误差分析', icon: 'Monitor' },
]

const showShell = computed(() => isAuthenticated.value && route.name !== 'Login')
const currentPage = computed(() => navItems.find(item => item.path === route.path))
const currentPageTitle = computed(() => route.meta?.title || currentPage.value?.label || '新能源调度系统')
const currentPageSubtitle = computed(() => currentPage.value?.subtitle || '')
const displayUserName = computed(() => {
  const rawName = currentUser.value?.display_name || currentUser.value?.username || '用户'
  return rawName === 'System Admin' || rawName === 'admin' ? '系统管理员' : rawName
})

function updateTime() {
  currentTime.value = new Date().toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function handleLogout() {
  clearAuthSession()
  router.push('/login')
}

onMounted(() => {
  restoreAuthSession()
  updateTime()
  timer = setInterval(updateTime, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.app-root { display: flex; min-height: 100vh; background: var(--bg-primary); }
.app-sidebar { width: 248px; background: var(--bg-secondary); border-right: 1px solid var(--border-glass); display: flex; flex-direction: column; position: fixed; inset: 0 auto 0 0; transition: width var(--duration-normal) var(--ease-default); z-index: 100; }
.app-sidebar.collapsed { width: 68px; }
.sidebar-logo { height: 64px; display: flex; align-items: center; gap: 12px; padding: 0 20px; border: 0; border-bottom: 1px solid var(--border-glass); background: transparent; color: inherit; cursor: pointer; overflow: hidden; }
.logo-icon { align-items: center; background: var(--gradient-cyan); border-radius: var(--radius-sm); color: #06111f; display: inline-flex; font-size: 16px; font-weight: 900; height: 28px; justify-content: center; min-width: 28px; }
.logo-text { color: var(--accent-cyan); font-size: 14px; font-weight: 800; white-space: nowrap; }
.sidebar-nav { flex: 1; padding: var(--space-md) var(--space-sm); display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
.nav-item { display: flex; align-items: center; gap: 12px; min-height: 42px; padding: 10px 14px; border-radius: var(--radius-sm); color: var(--text-secondary); text-decoration: none; white-space: nowrap; overflow: hidden; position: relative; }
.nav-item:hover { color: var(--text-primary); background: var(--bg-hover); }
.nav-item.active { color: var(--accent-cyan); background: rgba(0, 212, 255, 0.11); }
.nav-item.active::before { content: ''; position: absolute; left: 0; top: 22%; bottom: 22%; width: 3px; border-radius: 0 2px 2px 0; background: var(--accent-cyan); }
.nav-label { font-size: 13px; font-weight: 600; }
.sidebar-footer { padding: var(--space-md); border-top: 1px solid var(--border-glass); }
.collapse-btn { width: 100%; display: flex; align-items: center; justify-content: center; padding: 8px; border: 1px solid var(--border-glass); background: var(--bg-hover); border-radius: var(--radius-sm); color: var(--text-secondary); cursor: pointer; }
.collapse-btn:hover { color: var(--text-primary); border-color: var(--border-active); }
.app-main { flex: 1; min-height: 100vh; margin-left: 248px; display: flex; flex-direction: column; transition: margin-left var(--duration-normal) var(--ease-default); }
.app-main.no-sidebar { margin-left: 0; }
.app-main.sidebar-collapsed { margin-left: 68px; }
.app-header { min-height: 64px; display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); padding: 10px var(--space-xl); border-radius: 0; border-width: 0 0 1px; position: sticky; top: 0; z-index: 50; background: rgba(10, 14, 39, 0.92) !important; }
.page-title { font-size: 16px; font-weight: 700; color: var(--text-primary); line-height: 1.25; }
.page-subtitle { margin-top: 2px; font-size: 12px; color: var(--text-secondary); }
.header-right { display: flex; align-items: center; gap: var(--space-md); }
.header-clock { color: var(--text-secondary); background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-full); font-size: 12px; padding: 5px 12px; }
.header-user { display: flex; align-items: center; gap: 8px; border: 1px solid var(--border-glass); border-radius: var(--radius-full); background: var(--bg-input); color: var(--text-secondary); cursor: pointer; font-size: 13px; padding: 6px 12px; }
.header-user:hover { color: var(--accent-red); border-color: rgba(255, 82, 82, 0.35); }
.logout-icon { font-size: 14px; }
.app-content { flex: 1; padding: var(--space-lg); overflow-y: auto; }

@media (max-width: 1199px) {
  .app-sidebar { width: 72px; }
  .app-sidebar:not(.collapsed) .logo-text,
  .app-sidebar:not(.collapsed) .nav-label { display: none; }
  .app-main,
  .app-main.sidebar-collapsed { margin-left: 72px; }
}

@media (max-width: 767px) {
  .app-root { flex-direction: column; }
  .app-sidebar,
  .app-sidebar.collapsed { position: sticky; width: 100%; height: auto; flex-direction: row; align-items: center; border-right: 0; border-bottom: 1px solid var(--border-glass); }
  .sidebar-logo { width: 56px; height: 56px; padding: 0 16px; border-bottom: 0; }
  .logo-text, .nav-label, .sidebar-footer { display: none; }
  .sidebar-nav { flex-direction: row; padding: 8px; overflow-x: auto; }
  .nav-item { flex: 0 0 40px; width: 40px; height: 40px; justify-content: center; padding: 0; }
  .nav-item.active::before { left: 20%; right: 20%; top: auto; bottom: 0; width: auto; height: 3px; }
  .app-main,
  .app-main.sidebar-collapsed { margin-left: 0; }
  .app-header { align-items: flex-start; padding: 10px 16px; }
  .header-right { justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
  .header-clock { display: none; }
  .page-title { font-size: 14px; }
  .page-subtitle { display: none; }
  .app-content { padding: 16px; }
}
</style>
