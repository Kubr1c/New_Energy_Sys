import api from '../utils/api'

function asArray(value) {
  return Array.isArray(value) ? value : []
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null
}

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

export async function fetchStage21Report() {
  const res = await api.get('/api/stage21/report')
  return res.data || null
}

export async function fetchStage21WeatherPredictions() {
  const res = await api.get('/api/stage21/weather-predictions')
  return res.data || []
}

export async function fetchStage21PriceScenarios() {
  const res = await api.get('/api/stage21/price-scenarios')
  return res.data || []
}

export async function fetchStage21DispatchResults() {
  const res = await api.get('/api/stage21/dispatch-results')
  return res.data || []
}

export async function fetchStage21DispatchMetrics() {
  const res = await api.get('/api/stage21/dispatch-metrics')
  return res.data || []
}

export async function fetchRealtimeWeatherForecast(params) {
  const res = await api.get('/api/weather/forecast', { params })
  return res.data || null
}

export async function runWeatherDispatchExperiment(payload) {
  const res = await api.post('/api/dispatch/weather-experiment/run', payload)
  return asObject(res.data)
}

export async function fetchWeatherDispatchExperimentRuns(limit = 20) {
  const res = await api.get('/api/dispatch/weather-experiment/runs', { params: { limit } })
  return asArray(res.data)
}

export async function fetchWeatherDispatchExperimentRun(runId) {
  const res = await api.get(`/api/dispatch/weather-experiment/runs/${runId}`)
  return asObject(res.data)
}

export async function exportWeatherDispatchExperimentRun(runId, format) {
  const res = await api.get(`/api/dispatch/weather-experiment/runs/${runId}/export`, {
    params: { format },
    responseType: 'blob',
  })
  return { blob: res.data, headers: res.headers }
}
