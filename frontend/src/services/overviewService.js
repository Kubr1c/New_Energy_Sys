import api from '../utils/api'

export async function fetchOverviewBundle() {
  const [configRes, metricsRes, qualityRes, featureRes, dispatchRes] = await Promise.allSettled([
    api.get('/api/config'),
    api.get('/api/models/main'),
    api.get('/api/data/quality'),
    api.get('/api/data/features'),
    api.get('/api/rawhide/dispatch-metrics'),
  ])

  return {
    siteConfig: configRes.status === 'fulfilled' ? (configRes.value.data || {}) : {},
    mainMetrics: metricsRes.status === 'fulfilled' ? (metricsRes.value.data || []) : [],
    quality: qualityRes.status === 'fulfilled' ? (qualityRes.value.data || {}) : {},
    featureReport: featureRes.status === 'fulfilled' ? (featureRes.value.data || {}) : {},
    dispatchMetrics: dispatchRes.status === 'fulfilled' ? (dispatchRes.value.data || []) : [],
  }
}
