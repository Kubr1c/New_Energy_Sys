import api from '../utils/api'

export async function fetchOverviewBundle() {
  const [configRes, metricsRes] = await Promise.all([
    api.get('/api/config'),
    api.get('/api/models/main'),
  ])

  return {
    siteConfig: configRes.data || {},
    mainMetrics: metricsRes.data || [],
  }
}

export async function fetchMainPredictions(limit) {
  const res = await api.get('/api/predictions/main', { params: { limit } })
  return res.data || []
}
