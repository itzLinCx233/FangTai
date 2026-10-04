import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, getToken, setToken, clearToken } from '../api'

export interface User {
  id: number
  username: string
  nickname: string
  role: 'user' | 'admin'
  health_profile_id: number | null
  phone: string | null
  is_active: boolean
  created_at: string
  last_login_at: string
}

export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isLoggedIn = computed(() => !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function refresh() {
    if (!getToken()) { user.value = null; return }
    try { user.value = await api<User>('/api/auth/me') }
    catch { clearToken(); user.value = null }
  }

  async function login(username: string, password: string) {
    const d = await api<{ token: string; user: User }>('/api/auth/login', {
      method: 'POST', body: JSON.stringify({ username, password }),
    })
    setToken(d.token); user.value = d.user
    return d.user
  }

  async function register(payload: { username: string; password: string; nickname: string; health_profile_id: number | null }) {
    const d = await api<{ token: string; user: User }>('/api/auth/register', {
      method: 'POST', body: JSON.stringify(payload),
    })
    setToken(d.token); user.value = d.user
    return d.user
  }

  function logout() { clearToken(); user.value = null }

  return { user, isLoggedIn, isAdmin, refresh, login, register, logout }
})
