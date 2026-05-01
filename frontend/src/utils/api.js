import axios from 'axios'
import { clearAuthSession, getAuthToken } from '../stores/authState'

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

api.interceptors.request.use(config => {
  // Legacy services may pass /api-prefixed paths. baseURL already targets /api,
  // so strip the duplicate prefix before the request leaves the browser.
  if (config.url?.startsWith('/api/')) {
    config.url = config.url.slice('/api'.length)
  }

  const token = getAuthToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  res => res,
  err => {
    const normalized = normalizeApiError(err)
    if (normalized.isAuthError) {
      clearAuthSession()
      window.location.hash = '#/login'
    }
    err.normalized = normalized
    return Promise.reject(err)
  }
)

export default api
