import api from '../utils/api'

export async function fetchReportStages() {
  const res = await api.get('/api/reports/list')
  return res.data || []
}

export async function fetchReportMarkdown(stageId) {
  const res = await api.get(`/api/reports/${stageId}/md`)
  return res.data?.content || ''
}
