import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './stores/auth'
import { getToken } from './api'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/', name: 'chat', component: () => import('./views/ChatView.vue') },
    { path: '/admin', name: 'admin', component: () => import('./views/AdminView.vue') },
  ],
})

// 全局路由守卫：未登录一律先去登录页（评测走 /api 接口不受影响）
router.beforeEach(async (to) => {
  const auth = useAuth()
  if (!auth.isLoggedIn && getToken()) await auth.refresh()
  if (to.meta.public) {
    if (auth.isLoggedIn && to.path === '/login') {
      return to.query.redirect ? String(to.query.redirect) : '/'
    }
    return true
  }
  if (!auth.isLoggedIn) {
    return { path: '/login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  return true
})
