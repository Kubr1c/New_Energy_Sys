<template>
  <div class="login-container">
    <!-- Animated background grid -->
    <div class="bg-grid"></div>
    <div class="bg-glow"></div>

    <div class="login-card glass-card">
      <div class="login-header">
        <div class="login-logo">⚡</div>
        <h1 class="login-title">NES Platform</h1>
        <p class="login-subtitle">基于深度学习的新能源储能侧优化调度系统</p>
        <p class="login-subtitle-en">Deep Learning-based New Energy Storage Dispatch System</p>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
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
            <input v-model="password" type="password" placeholder="Enter password" autocomplete="current-password" @keyup.enter="handleLogin" />
          </div>
        </div>

        <button type="submit" class="login-btn" :disabled="loading">
          <span v-if="!loading">登录 Sign In</span>
          <span v-else class="btn-loading">Signing in...</span>
        </button>

        <p v-if="error" class="login-error">{{ error }}</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../utils/api'
import { normalizeApiError } from '../utils/api'

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
    localStorage.setItem('nes_token', res.data.token)
    localStorage.setItem('nes_user', JSON.stringify(res.data.user))
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

/* Animated grid background */
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0,212,255,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,212,255,0.03) 1px, transparent 1px);
  background-size: 60px 60px;
  animation: gridMove 20s linear infinite;
}
@keyframes gridMove {
  0% { transform: translate(0, 0); }
  100% { transform: translate(60px, 60px); }
}

.bg-glow {
  position: absolute;
  width: 600px; height: 600px;
  background: radial-gradient(circle, rgba(0,212,255,0.08) 0%, transparent 70%);
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  animation: pulse 4s ease-in-out infinite;
}

.login-card {
  width: 420px;
  padding: 48px 40px;
  z-index: 10;
  animation: fadeInUp 0.8s var(--ease-default);
}

.login-header {
  text-align: center;
  margin-bottom: 36px;
}
.login-logo {
  font-size: 48px;
  margin-bottom: 12px;
  filter: drop-shadow(0 0 20px rgba(0, 212, 255, 0.6));
  animation: glow 3s ease-in-out infinite;
}
.login-title {
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 800;
  color: var(--accent-cyan);
  letter-spacing: 0.08em;
  margin-bottom: 12px;
}
.login-subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.login-subtitle-en {
  font-size: 11px;
  color: var(--text-tertiary);
}

.login-form { display: flex; flex-direction: column; gap: 20px; }
.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}
.input-icon {
  position: absolute;
  left: 14px;
  color: var(--text-tertiary);
  z-index: 1;
}
.input-wrapper input {
  width: 100%;
  padding: 12px 14px 12px 42px;
  background: var(--bg-input);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-size: 14px;
  font-family: var(--font-body);
  outline: none;
  transition: all var(--duration-fast) var(--ease-default);
}
.input-wrapper input:focus {
  border-color: var(--accent-cyan);
  box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.1);
}
.input-wrapper input::placeholder { color: var(--text-tertiary); }

.login-btn {
  width: 100%;
  padding: 14px;
  background: var(--gradient-cyan);
  border: none;
  border-radius: var(--radius-md);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-default);
  margin-top: 8px;
}
.login-btn:hover { transform: translateY(-1px); box-shadow: var(--shadow-glow-cyan); }
.login-btn:active { transform: translateY(0); }
.login-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

.login-error {
  text-align: center;
  color: var(--accent-red);
  font-size: 13px;
}

@media (max-width: 640px) {
  .login-container { align-items: flex-start; padding: 32px 16px; overflow-y: auto; }
  .login-card { width: min(100%, 420px); padding: 32px 24px; }
  .login-title { font-size: 24px; }
  .login-subtitle { font-size: 13px; }
}
</style>
