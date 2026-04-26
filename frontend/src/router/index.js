import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    name: 'Overview',
    component: () => import('../views/OverviewDashboard.vue'),
    meta: { requiresAuth: true, title: '系统总览 Overview' },
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('../views/ModelComparison.vue'),
    meta: { requiresAuth: true, title: '模型对比 Model Comparison' },
  },
  {
    path: '/dispatch',
    name: 'Dispatch',
    component: () => import('../views/DispatchSimulation.vue'),
    meta: { requiresAuth: true, title: '调度仿真 Dispatch Simulation' },
  },
  {
    path: '/governance',
    name: 'Governance',
    component: () => import('../views/GovernanceAnalysis.vue'),
    meta: { requiresAuth: true, title: '策略治理 Strategy Governance' },
  },
  {
    path: '/data',
    name: 'Data',
    component: () => import('../views/DataExplorer.vue'),
    meta: { requiresAuth: true, title: '数据探索 Data Explorer' },
  },
  {
    path: '/reports',
    name: 'Reports',
    component: () => import('../views/ReportViewer.vue'),
    meta: { requiresAuth: true, title: '实验报告 Reports' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth !== false) {
    const token = localStorage.getItem('nes_token')
    if (!token) {
      next('/login')
      return
    }
  }
  next()
})

export default router
