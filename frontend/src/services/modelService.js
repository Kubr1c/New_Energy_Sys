import api from '../utils/api'

export async function fetchModelComparison() {
  const [tabularRes, deepLearningRes] = await Promise.all([
    api.get('/api/models/tabular'),
    api.get('/api/models/deep-learning'),
  ])

  return {
    tabularMetrics: tabularRes.data || [],
    deepLearningMetrics: deepLearningRes.data || [],
  }
}
