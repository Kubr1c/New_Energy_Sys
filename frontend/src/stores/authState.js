import { computed, reactive } from 'vue'

const TOKEN_KEY = 'nes_token'
const USER_KEY = 'nes_user'

function readStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

const state = reactive({
  token: localStorage.getItem(TOKEN_KEY),
  user: readStoredUser(),
})

export const isAuthenticated = computed(() => Boolean(state.token))
export const currentUser = computed(() => state.user)

export function setAuthSession(token, user) {
  state.token = token
  state.user = user || null
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user || null))
}

export function clearAuthSession() {
  state.token = null
  state.user = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function restoreAuthSession() {
  state.token = localStorage.getItem(TOKEN_KEY)
  state.user = readStoredUser()
}

export function getAuthToken() {
  return state.token || localStorage.getItem(TOKEN_KEY)
}
