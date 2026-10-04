// 统一 API 客户端：带 token、错误展开 detail
export const TOKEN_KEY = 'ft_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  opts.headers = Object.assign(
    { 'Content-Type': 'application/json' },
    getToken() ? { Authorization: 'Bearer ' + getToken() } : {},
    (opts.headers as Record<string, string>) || {},
  )
  const r = await fetch(path, opts)
  if (!r.ok) {
    let msg = r.status + ' 错误'
    try { const j = await r.json(); msg = j.detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

// 健康档案类型
export interface Profile {
  id: number
  性别: string
  年龄: number
  特殊人群?: string[]
  过敏食材?: string[]
  [k: string]: unknown
}

export function profileLabel(p: Profile): string {
  const crowd = (p['特殊人群'] || []).length ? ' ' + p['特殊人群'].join('/') : ''
  const allergy = (p['过敏食材'] || []).length ? ' 过敏:' + p['过敏食材'].join('/') : ''
  return `${p.id}号 ${p['性别']} ${p['年龄']}岁${crowd}${allergy}`
}
