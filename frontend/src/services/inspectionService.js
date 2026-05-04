import api from '../utils/api'

/**
 * Fetch inspection metadata: available date range, horizons, experiments, model info.
 * GET /api/predictions/metadata
 */
export async function fetchInspectionMetadata() {
  const res = await api.get('/predictions/metadata')
  return res.data
}

/**
 * Fetch inspection prediction data with filters.
 * GET /api/predictions/inspect
 * @param {Object} params - Query params (start, end, horizons, experiments, granularity)
 */
export async function fetchInspectionData(params) {
  const res = await api.get('/predictions/inspect', { params })
  return res.data
}
