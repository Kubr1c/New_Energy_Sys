import api from '../utils/api'

export async function fetchSensitivityMetrics() {
  const res = await api.get('/api/sensitivity/metrics')
  return res.data || []
}
