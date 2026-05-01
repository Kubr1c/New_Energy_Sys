<template>
  <div class="login-container">
    <div class="bg-grid"></div>
    <div class="bg-glow"></div>

    <div class="login-card glass-card">
      <div class="login-header">
        <div class="login-logo">⚡</div>
        <h1 class="login-title">NES Platform</h1>
        <p class="login-subtitle">基于深度学习的新能源储能侧优化调度系统</p>
        <p class="login-subtitle-en">Deep Learning-based New Energy Storage Dispatch System</p>
      </div>

      <form class="login-form" @submit.prevent="handleLogin">
        <div class="form-group">
          <label>用户名 Username</label>
          <div class="input-wrapper">
            <el-icon class="input-icon"><User /></el-icon>
            <input v-model="username" type="text" placeholder="Enter username" autocomplete="username" />
          </div>
        </div>

        <div class="form-group">
          <label>密码 Password</label>
          <div class="input-wrapper">
            <el-icon class="input-icon"><Lock /></el-icon>
            <input
              v-model="password"
              type="password"
              placeholder="Enter password"
              autocomplete="current-password"
              @keyup.enter="handleLogin"
            />
          </div>
        </div>

        <button type="submit" class="login-btn" :disabled="loading">
          <span v-if="!loading">登录 Sign In</span>
          <span v-else>Signing in...</span>
        </button>

        <p v-if="error" class="login-error">{{ error }}</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api, { normalizeApiError } from '../utils/api'
import { setAuthSession } from '../stores/authState'

const router = useRouter()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }

  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/api/auth/login', {
      username: username.value,
      password: password.value,
    })
    setAuthSession(res.data.token, res.data.user)
    router.push('/')
  } catch (err) {
    const apiError = err.normalized || normalizeApiError(err)
    error.value = apiError.status === 401 ? '用户名或密码错误' : apiError.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: var(--bg-primary);
}
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 212, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 212, 255, 0.04) 1px, transparent 1px);
  background-size: 60px 60px;
}
.bg-glow {
  position: absolute;
  width: 620px;
  height: 620px;
  background: radial-gradient(circle, rgba(0, 212, 255, 0.11) 0%, transparent 68%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
}
.login-card {
  width: 440px;
  padding: 44px 40px;
  z-index: 1;
}
.login-header { text-align: center; margin-bottom: 34px; }
.login-logo { font-size: 42px; margin-bottom: 12px; }
.login-title {
  color: var(--accent-cyan);
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 800;
  letter-spacing: 0.08em;
  margin-bottom: 12px;
}
.login-subtitle { color: var(--text-secondary); font-size: 14px; margin-bottom: 4px; }
.login-subtitle-en { color: var(--text-tertiary); font-size: 12px; }
.login-form { display: flex; flex-direction: column; gap: 20px; }
.form-group label {
  display: block;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
}
.input-wrapper { position: relative; display: flex; align-items: center; }
.input-icon { position: absolute; left: 14px; color: var(--text-tertiary); z-index: 1; }
.input-wrapper input {
  width: 100%;
  padding: 12px 14px 12px 42px;
  background: var(--bg-input);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font: inherit;
  outline: none;
}
.input-wrapper input:focus {
  border-color: var(--accent-cyan);
  box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.12);
}
.input-wrapper input::placeholder { color: var(--text-tertiary); }
.login-btn {
  width: 100%;
  padding: 14px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--gradient-cyan);
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 15px;
  font-weight: 700;
  margin-top: 8px;
}
.login-btn:hover { box-shadow: var(--shadow-glow-cyan); }
.login-btn:disabled { cursor: not-allowed; opacity: 0.65; }
.login-error { color: var(--accent-red); font-size: 13px; text-align: center; }

@media (max-width: 640px) {
  .login-container { align-items: flex-start; padding: 32px 16px; overflow-y: auto; }
  .login-card { width: min(100%, 440px); padding: 32px 24px; }
  .login-title { font-size: 24px; }
}
</style>
