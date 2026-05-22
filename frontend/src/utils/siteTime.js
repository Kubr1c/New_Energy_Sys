export const REFERENCE_SITE_TIME_ZONE = 'America/Denver'
export const REFERENCE_SITE_TIME_LABEL = '电站当地时间'

export function formatReferenceSiteHour(value, fallback = '-') {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value || fallback)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    hour12: false,
    timeZone: REFERENCE_SITE_TIME_ZONE,
  })
}

export function formatReferenceSiteSecond(value, fallback = '-') {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value || fallback)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: REFERENCE_SITE_TIME_ZONE,
  })
}
