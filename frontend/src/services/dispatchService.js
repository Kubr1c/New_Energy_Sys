import api from '../utils/api'

export async function fetchGovernanceScorecard() {
  const res = await api.get('/api/governance/scorecard')
  return res.data || []
}

export async function fetchRawhideReport() {
  const res = await api.get('/api/rawhide/report')
  return res.data || null
}

export async function fetchRawhideDispatchMetrics() {
  const res = await api.get('/api/rawhide/dispatch-metrics')
  return res.data || []
}

export async function fetchRawhideSensitivityMetrics() {
  const res = await api.get('/api/rawhide/sensitivity-metrics')
  return res.data || []
}

export async function fetchRawhideDegradationMetrics() {
  const res = await api.get('/api/rawhide/degradation-metrics')
  return res.data || []
}
