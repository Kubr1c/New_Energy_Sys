import api from '../utils/api'

export async function fetchDataExplorerBundle() {
  const [qualityRes, featuresRes, commandsRes] = await Promise.all([
    api.get('/api/data/quality'),
    api.get('/api/features/importance', { params: { top_n: 20 } }),
    api.get('/api/tasks/commands'),
  ])

  return {
    quality: qualityRes.data || {},
    features: featuresRes.data || [],
    commands: commandsRes.data || [],
  }
}

export async function fetchTasks() {
  const res = await api.get('/api/tasks')
  return res.data || []
}

export async function submitTaskCommand(commandId) {
  const res = await api.post('/api/tasks/submit', { command_id: commandId })
  return res.data
}

export async function fetchTaskStatus(taskId) {
  const res = await api.get(`/api/tasks/${taskId}`)
  return res.data
}
