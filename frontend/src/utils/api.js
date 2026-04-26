/**
 * Axios API client with JWT auth interceptors.
 *
 * 生产部署默认走同源 `/api`，由 FastAPI 或反向代理转发后端接口。
 * 只有显式配置 `VITE_API_BASE` 时才覆盖默认值，避免生产包把请求打到终端用户本机 localhost。
 */
import axios from 'axios'

export function normalizeApiError(err) {
  const status = err.response?.status ?? 0
  const detail = err.response?.data?.detail
  const fallback = status === 403 ? '当前账号权限不足' : '请求失败，请稍后重试'

  return {
    status,
    message: typeof detail === 'string' ? detail : fallback,
    requestId: err.response?.headers?.['x-request-id'] || null,
    isAuthError: status === 401,
  }
}

const api = axios.create({
  baseURL: import.meta.env?.VITE_API_BASE || '/api',
  timeout: 120000,
})

// Request interceptor — attach JWT
api.interceptors.request.use(config => {
  // 兼容既有页面中带 API 前缀的写法：baseURL 已经是同源 API 入口时，
  // 这里去掉重复前缀，避免生产请求生成双重 API 路径。
  if (config.url?.startsWith('/api/')) {
    config.url = config.url.slice('/api'.length)
  }
  const token = localStorage.getItem('nes_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor — handle 401
api.interceptors.response.use(
  res => res,
  err => {
    const normalized = normalizeApiError(err)
    if (normalized.isAuthError) {
      localStorage.removeItem('nes_token')
      localStorage.removeItem('nes_user')
      window.location.hash = '#/login'
    }
    err.normalized = normalized
    return Promise.reject(err)
  }
)

export default api
