import { createRouter, createWebHashHistory } from 'vue-router'
import { isAuthenticated, restoreAuthSession } from '../stores/authState'

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
    meta: { requiresAuth: true, title: '系统总览' },
  },
  {
    path: '/models',
    name: 'Models',
    component: () => import('../views/ModelComparison.vue'),
    meta: { requiresAuth: true, title: '模型评估' },
  },
  {
    path: '/dispatch',
    name: 'Dispatch',
    component: () => import('../views/DispatchSimulation.vue'),
    meta: { requiresAuth: true, title: '调度分析' },
  },
  {
    path: '/governance',
    name: 'Governance',
    component: () => import('../views/GovernanceAnalysis.vue'),
    meta: { requiresAuth: true, title: '配置治理' },
  },
  {
    path: '/data',
    name: 'Data',
    component: () => import('../views/DataExplorer.vue'),
    meta: { requiresAuth: true, title: '数据管理' },
  },
  {
    path: '/reports',
    name: 'Reports',
    component: () => import('../views/ReportViewer.vue'),
    meta: { requiresAuth: true, title: '实验报告' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  restoreAuthSession()
  if (to.meta.requiresAuth !== false && !isAuthenticated.value) {
    next('/login')
    return
  }
  next()
})

export default router
