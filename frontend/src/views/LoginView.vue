<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="logo">🍲</div>
        <h1>个性化膳食规划 Agent</h1>
        <p>方太 AI 专项赛 · 登录后开始您的健康膳食之旅</p>
      </div>

      <div class="tabs">
        <div :class="{ on: mode === 'login' }" @click="mode = 'login'">登 录</div>
        <div :class="{ on: mode === 'register' }" @click="mode = 'register'">注 册</div>
      </div>

      <div class="err">{{ errMsg }}</div>

      <template v-if="mode === 'login'">
        <div class="form-item">
          <label>账号</label>
          <input v-model="li.account" placeholder="请输入账号" autocomplete="username" @keyup.enter="doLogin">
        </div>
        <div class="form-item">
          <label>密码</label>
          <input v-model="li.pass" type="password" placeholder="请输入密码" autocomplete="current-password" @keyup.enter="doLogin">
        </div>
        <button class="btn primary submit" :disabled="loading" @click="doLogin">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
        <p class="switch-line">还没有账号？<a @click="mode = 'register'">立即注册</a></p>
      </template>

      <template v-else>
        <div class="form-item">
          <label>账号</label>
          <input v-model="rg.account" placeholder="3-32位，字母/数字/下划线/中文" autocomplete="username">
        </div>
        <div class="form-item">
          <label>昵称</label>
          <input v-model="rg.nick" placeholder="怎么称呼您（可空）">
        </div>
        <div class="form-item">
          <label>密码</label>
          <input v-model="rg.pass" type="password" placeholder="至少6位" autocomplete="new-password" @keyup.enter="doRegister">
        </div>
        <div class="form-item">
          <label>确认密码</label>
          <input v-model="rg.pass2" type="password" placeholder="再次输入密码" @keyup.enter="doRegister">
        </div>
        <button class="btn primary submit" :disabled="loading" @click="doRegister">
          {{ loading ? '注册中…' : '注 册' }}
        </button>
        <p class="switch-line">已有账号？<a @click="mode = 'login'">直接登录</a></p>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const errMsg = ref('')
const li = ref({ account: '', pass: '' })
const rg = ref({ account: '', nick: '', pass: '', pass2: '' })

function goAfterLogin() {
  const redirect = route.query.redirect ? String(route.query.redirect) : '/'
  router.replace(redirect)
}

async function doLogin() {
  errMsg.value = ''
  if (!li.value.account.trim() || !li.value.pass) { errMsg.value = '请输入账号和密码'; return }
  loading.value = true
  try {
    await auth.login(li.value.account.trim(), li.value.pass)
    goAfterLogin()
  } catch (e: any) { errMsg.value = e.message }
  finally { loading.value = false }
}

async function doRegister() {
  errMsg.value = ''
  const a = rg.value.account.trim()
  if (a.length < 3) { errMsg.value = '账号至少 3 位'; return }
  if (rg.value.pass.length < 6) { errMsg.value = '密码至少 6 位'; return }
  if (rg.value.pass !== rg.value.pass2) { errMsg.value = '两次输入的密码不一致'; return }
  loading.value = true
  try {
    await auth.register({ username: a, password: rg.value.pass, nickname: rg.value.nick.trim() })
    goAfterLogin()
  } catch (e: any) { errMsg.value = e.message }
  finally { loading.value = false }
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0f6f5c 0%, #14866d 45%, #1a9b7e 100%);
  padding: 20px;
}
.login-card {
  width: 400px; max-width: 100%;
  background: #fff; border-radius: 18px; padding: 34px 34px 26px;
  box-shadow: 0 24px 80px rgba(6, 46, 37, .35);
}
.brand { text-align: center; margin-bottom: 22px }
.logo {
  width: 56px; height: 56px; border-radius: 15px; margin: 0 auto 12px;
  background: linear-gradient(135deg, #0f6f5c, #1a9b7e);
  display: flex; align-items: center; justify-content: center; font-size: 28px;
  box-shadow: 0 6px 16px rgba(15, 111, 92, .3);
}
.brand h1 { font-size: 19px; color: var(--text) }
.brand p { font-size: 12.5px; color: var(--muted); margin-top: 5px }
.tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 16px }
.tabs div {
  flex: 1; text-align: center; padding: 11px; cursor: pointer; font-size: 14.5px;
  color: var(--muted); border-bottom: 2px solid transparent; user-select: none;
}
.tabs div.on { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600 }
.form-item { margin-bottom: 14px }
.form-item label { display: block; font-size: 13px; color: #4a5361; margin-bottom: 6px }
.form-item input {
  width: 100%; padding: 10px 12px; border: 1px solid var(--border);
  border-radius: 9px; font-size: 14px; outline: none; font-family: inherit;
  transition: border-color .15s;
}
.form-item input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(15, 111, 92, .08) }
.err { color: var(--danger); font-size: 13px; min-height: 18px; margin-bottom: 6px; text-align: center }
.submit { width: 100%; padding: 11px; font-size: 15px; margin-top: 4px }
.switch-line { text-align: center; font-size: 13px; color: var(--muted); margin-top: 14px }
.switch-line a { color: var(--primary); cursor: pointer; font-weight: 500 }
</style>
