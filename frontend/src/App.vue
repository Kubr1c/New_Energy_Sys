<template>
  <div class="app-root light-admin">
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
  { path: '/', label: '仪表盘', subtitle: '系统总览', icon: 'DataAnalysis' },
  { path: '/data', label: '数据管理', subtitle: '样本与字段状态', icon: 'DataLine' },
  { path: '/models', label: '预测分析', subtitle: '模型误差对比', icon: 'TrendCharts' },
  { path: '/dispatch', label: '优化调度', subtitle: '储能参数与调度结果', icon: 'Setting' },
  { path: '/governance', label: '配置分析', subtitle: '容量功率敏感性', icon: 'Histogram' },
  { path: '/inspect', label: '预测验收', subtitle: '预测曲线验收', icon: 'Monitor' },
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
.app-root {
  display: flex;
  min-height: 100vh;
  background: var(--bg-primary);
}
.app-root.light-admin {
  --bg-primary: #f4f6f9;
  --bg-secondary: #2d4059;
  --bg-card: #ffffff;
  --bg-card-solid: #ffffff;
  --bg-glass: #ffffff;
  --bg-input: #ffffff;
  --bg-hover: rgba(64, 158, 255, 0.08);
  --border-glass: #e5e9f2;
  --border-active: #409eff;
  --text-primary: #303133;
  --text-secondary: #606266;
  --text-tertiary: #909399;
  --text-accent: #409eff;
  --accent-cyan: #409eff;
  --accent-green: #67c23a;
  --accent-orange: #e6a23c;
  --accent-red: #f56c6c;
  --accent-purple: #8e7cc3;
  --accent-blue: #409eff;
  --accent-yellow: #f4c542;
  --gradient-cyan: linear-gradient(135deg, #409eff 0%, #2f80d8 100%);
  --gradient-green: linear-gradient(135deg, #67c23a 0%, #4aa52c 100%);
  --gradient-orange: linear-gradient(135deg, #e6a23c 0%, #d9822b 100%);
  --gradient-purple: linear-gradient(135deg, #8e7cc3 0%, #6f60ad 100%);
  --shadow-card: 0 2px 12px rgba(31, 45, 61, 0.08);
  color: var(--text-primary);
}
.app-sidebar { width: 208px; background: var(--bg-secondary); border-right: 1px solid rgba(255,255,255,0.08); display: flex; flex-direction: column; position: fixed; inset: 0 auto 0 0; transition: width var(--duration-normal) var(--ease-default); z-index: 100; }
.app-sidebar.collapsed { width: 68px; }
.sidebar-logo { height: 64px; display: flex; align-items: center; gap: 10px; padding: 0 18px; border: 0; border-bottom: 1px solid rgba(255,255,255,0.08); background: transparent; color: inherit; cursor: pointer; overflow: hidden; }
.logo-icon { align-items: center; background: rgba(255,255,255,0.14); border-radius: 4px; color: #ffffff; display: inline-flex; font-size: 15px; font-weight: 800; height: 28px; justify-content: center; min-width: 28px; }
.logo-text { color: #ffffff; font-size: 15px; font-weight: 700; white-space: nowrap; }
.sidebar-nav { flex: 1; padding: var(--space-md) var(--space-sm); display: flex; flex-direction: column; gap: 6px; overflow-y: auto; }
.nav-item { display: flex; align-items: center; gap: 12px; min-height: 46px; padding: 11px 16px; border-radius: 0; color: rgba(255,255,255,0.68); text-decoration: none; white-space: nowrap; overflow: hidden; position: relative; }
.nav-item:hover { color: #ffffff; background: rgba(255,255,255,0.08); }
.nav-item.active { color: #409eff; background: rgba(0,0,0,0.10); }
.nav-item.active::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; border-radius: 0; background: #409eff; }
.nav-label { font-size: 14px; font-weight: 600; }
.sidebar-footer { padding: var(--space-md); border-top: 1px solid var(--border-glass); }
.collapse-btn { width: 100%; display: flex; align-items: center; justify-content: center; padding: 8px; border: 1px solid var(--border-glass); background: var(--bg-hover); border-radius: var(--radius-sm); color: var(--text-secondary); cursor: pointer; }
.collapse-btn:hover { color: var(--text-primary); border-color: var(--border-active); }
.app-main { flex: 1; min-height: 100vh; margin-left: 208px; display: flex; flex-direction: column; transition: margin-left var(--duration-normal) var(--ease-default); }
.app-main.no-sidebar { margin-left: 0; }
.app-main.sidebar-collapsed { margin-left: 68px; }
.app-header { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: var(--space-lg); padding: 10px 22px; border-radius: 0; border-width: 0 0 1px; position: sticky; top: 0; z-index: 50; background: #ffffff !important; box-shadow: 0 1px 8px rgba(31, 45, 61, 0.06); }
.page-title { font-size: 14px; font-weight: 700; color: #303133; line-height: 1.25; }
.page-title::before { content: '首页 / '; color: #909399; font-weight: 600; }
.page-subtitle { display: none; }
.header-right { display: flex; align-items: center; gap: var(--space-md); }
.header-clock { display: none; }
.header-user { display: flex; align-items: center; gap: 8px; border: 0; border-radius: 4px; background: transparent; color: #909399; cursor: pointer; font-size: 13px; padding: 6px 8px; }
.header-user:hover { color: var(--accent-red); border-color: rgba(255, 82, 82, 0.35); }
.logout-icon { font-size: 14px; }
.app-content { flex: 1; padding: 22px; overflow-y: auto; }

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
