<template>
  <div class="admin-page">
    <!-- 登录门 -->
    <div v-if="!entered" class="gate">
      <h2>需要管理员登录</h2>
      <p>{{ gateMsg }}</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="账号"><el-input v-model="gUser" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="gPass" type="password" show-password /></el-form-item>
      </el-form>
      <el-alert v-if="gErr" :title="gErr" type="error" :closable="false" style="margin-bottom: 12px" />
      <el-button type="primary" style="width: 100%" @click="doGateLogin">登 录</el-button>
      <div class="gate-links"><a href="/">← 返回聊天页</a></div>
    </div>

    <!-- 控制台 -->
    <el-container v-else class="console">
      <el-aside width="210px" class="side">
        <div class="brand"><h2>🍲 膳食 Agent</h2><p>管理后台 v1.1</p></div>
        <el-menu :default-active="view" background-color="#1d2b33" text-color="#cfd8de"
                 active-text-color="#ffffff" @select="onNav">
          <el-menu-item index="dash">📊 仪表盘</el-menu-item>
          <el-menu-item index="users">👥 用户管理</el-menu-item>
          <el-menu-item index="sessions">🕘 会话记录</el-menu-item>
          <el-menu-item index="settings">⚙️ 模型设置</el-menu-item>
          <el-menu-item index="chat">💬 返回聊天页</el-menu-item>
        </el-menu>
        <div class="foot">登录身份：{{ auth.user?.nickname || auth.user?.username }}<br>
          <a @click="doLogout">退出登录</a> · <a href="/">聊天页 ↗</a></div>
      </el-aside>

      <el-main>
        <div class="topbar">
          <h1>{{ viewTitle }}</h1>
          <div class="who">管理员：{{ auth.user?.nickname || auth.user?.username }}（{{ auth.user?.username }}）</div>
        </div>

        <!-- 仪表盘 -->
        <section v-show="view === 'dash'">
          <div class="cards">
            <div v-for="c in statCards" :key="c.label" class="card">
              <div class="ico" :style="{ background: c.bg, color: c.color }">◆</div>
              <div class="num">{{ c.num }}</div>
              <div class="lbl">{{ c.label }}</div>
            </div>
          </div>
          <el-card shadow="never">
            <template #header>最新注册用户</template>
            <el-table :data="recentUsers" size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="username" label="用户名" />
              <el-table-column prop="nickname" label="昵称" />
              <el-table-column label="角色" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.role === 'admin' ? 'warning' : 'success'" size="small">
                    {{ row.role === 'admin' ? '管理员' : '用户' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="注册时间" />
            </el-table>
          </el-card>
        </section>

        <!-- 用户管理 -->
        <section v-show="view === 'users'">
          <el-card shadow="never">
            <div class="toolbar">
              <el-input v-model="uSearch" placeholder="搜索账号 / 昵称…" style="width: 300px" clearable
                        @keyup.enter="uPage = 1; loadUsers()" />
              <el-button @click="uPage = 1; loadUsers()">搜索</el-button>
              <span class="flex1"></span>
              <el-button type="primary" @click="openCreate">＋ 新增用户</el-button>
            </div>
            <el-table :data="uRows" size="small">
              <el-table-column prop="id" label="ID" width="55" />
              <el-table-column prop="username" label="账号" min-width="100">
                <template #default="{ row }"><b>{{ row.username }}</b></template>
              </el-table-column>
              <el-table-column prop="nickname" label="昵称" min-width="90" />
              <el-table-column label="角色" width="85">
                <template #default="{ row }">
                  <el-tag :type="row.role === 'admin' ? 'warning' : 'success'" size="small">
                    {{ row.role === 'admin' ? '管理员' : '用户' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="手机号" width="110">
                <template #default="{ row }">{{ row.phone || '-' }}</template>
              </el-table-column>
              <el-table-column label="状态" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
                    {{ row.is_active ? '启用' : '禁用' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="注册时间" width="150" />
              <el-table-column label="最后登录" width="150">
                <template #default="{ row }">{{ row.last_login_at || '-' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="210" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openEdit(row)">编辑</el-button>
                  <el-button size="small" @click="resetPwd(row)">重置密码</el-button>
                  <el-button size="small" type="danger" :disabled="row.role === 'admin'"
                             :title="row.role === 'admin' ? '管理员账户不可删除' : ''"
                             @click="delUser(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination style="margin-top: 12px; justify-content: flex-end" layout="total, prev, pager, next"
                           :total="uTotal" :page-size="uSize" :current-page="uPage"
                           @current-change="(p: number) => { uPage = p; loadUsers() }" />
          </el-card>
        </section>

        <!-- 会话记录 -->
        <section v-show="view === 'sessions'">
          <el-card shadow="never">
            <div class="toolbar">
              <el-input v-model="sSearch" placeholder="搜索会话标题 / 用户名…" style="width: 300px" clearable
                        @keyup.enter="sPage = 1; loadSessions()" />
              <el-button @click="sPage = 1; loadSessions()">搜索</el-button>
            </div>
            <el-table :data="sRows" size="small">
              <el-table-column prop="session_id" label="会话ID" width="130">
                <template #default="{ row }"><span class="mono">{{ row.session_id }}</span></template>
              </el-table-column>
              <el-table-column label="标题" min-width="180">
                <template #default="{ row }">{{ row.title || '（未命名）' }}</template>
              </el-table-column>
              <el-table-column label="用户" min-width="110">
                <template #default="{ row }">{{ row.username }}{{ row.nickname && row.nickname !== row.username ? '（' + row.nickname + '）' : '' }}</template>
              </el-table-column>
              <el-table-column prop="msg_count" label="消息数" width="80" />
              <el-table-column prop="created_at" label="创建时间" width="160" />
              <el-table-column label="操作" width="90">
                <template #default="{ row }">
                  <el-button size="small" @click="viewSess(row.session_id)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination style="margin-top: 12px; justify-content: flex-end" layout="total, prev, pager, next"
                           :total="sTotal" :page-size="sSize" :current-page="sPage"
                           @current-change="(p: number) => { sPage = p; loadSessions() }" />
          </el-card>
        </section>
        <!-- 模型设置 -->
        <section v-show="view === 'settings'">
          <el-card shadow="never" style="max-width: 660px">
            <el-form label-width="110px" label-position="left">
              <el-form-item label="接口地址">
                <el-input v-model="lc.base_url" placeholder="https://open.bigmodel.cn/api/paas/v4" />
                <div class="hint">OpenAI 兼容端点，换服务商即改这里（智谱 / DeepSeek / vLLM / Ollama…）</div>
              </el-form-item>
              <el-form-item label="API Key">
                <el-input v-model="lc.api_key" type="password" show-password placeholder="输入新 Key 即覆盖" />
                <div class="hint">已保存：<span class="mono">{{ lcApiKeyMasked || '（空）' }}</span>，留空表示不修改</div>
              </el-form-item>
              <el-form-item label="主模型">
                <el-input v-model="lc.model" placeholder="glm-5.3" />
              </el-form-item>
              <el-form-item label="快速模型">
                <el-input v-model="lc.fast_model" placeholder="留空 = 与主模型相同（意图识别/选菜等轻量调用）" />
              </el-form-item>
              <el-form-item label="思考模式">
                <el-select v-model="lc.thinking" style="width: 100%">
                  <el-option label="disabled — 关闭思考（性能优先）" value="disabled" />
                  <el-option label="low — 低强度思考（均衡）" value="low" />
                  <el-option label="high — 高强度思考（质量优先，耗时明显增加）" value="high" />
                  <el-option label="max — 最高强度" value="max" />
                  <el-option label="auto — 交给服务端默认" value="auto" />
                </el-select>
              </el-form-item>
              <el-form-item label="温度">
                <el-slider v-model="lc.temperature" :min="0" :max="1.5" :step="0.05" show-input />
              </el-form-item>
            </el-form>
            <el-alert v-if="lc && lc.persisted === false"
                      title="当前为无 DB 模式：修改仅本进程生效，重启后回落环境变量默认值"
                      type="warning" :closable="false" style="margin-bottom: 12px" />
            <div class="lc-actions">
              <el-button :loading="lcTesting" @click="testLLM">测试连接</el-button>
              <el-button type="primary" :loading="lcSaving" @click="saveLLM">保存并生效</el-button>
            </div>
          </el-card>
        </section>
      </el-main>
    </el-container>

    <!-- 新增/编辑用户 -->
    <el-dialog v-model="umShow" :title="editingId == null ? '新增用户' : '编辑用户 #' + editingId" width="440px">
      <el-form label-width="90px">
        <el-form-item label="账号" v-if="editingId == null">
          <el-input v-model="um.username" placeholder="3-32位" />
        </el-form-item>
        <el-form-item label="账号" v-else>
          <el-input :model-value="um.username" disabled />
        </el-form-item>
        <el-form-item label="密码" v-if="editingId == null">
          <el-input v-model="um.password" type="password" show-password placeholder="至少6位" />
        </el-form-item>
        <el-form-item label="昵称"><el-input v-model="um.nickname" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="um.role" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机号"><el-input v-model="um.phone" /></el-form-item>
        <el-form-item label="状态" v-if="editingId != null">
          <el-switch v-model="um.active" active-text="启用" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="umShow = false">取消</el-button>
        <el-button type="primary" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 会话详情 -->
    <el-dialog v-model="cmShow" :title="'会话 ' + cmSid" width="640px">
      <div class="chat-view">
        <div v-for="(m, i) in cmItems" :key="i" class="cv-msg">
          <b>{{ m.role === 'user' ? '用户' : '助手' }}：</b>{{ m.content }}
        </div>
        <div v-if="!cmItems.length" class="muted">无消息</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuth } from '../stores/auth'

const router = useRouter()
const auth = useAuth()

const entered = ref(false)
const gateMsg = ref('请使用管理员账户登录后访问管理后台')
const gUser = ref('admin')
const gPass = ref('')
const gErr = ref('')

const view = ref<'dash' | 'users' | 'sessions' | 'settings'>('dash')
const viewTitle = computed(() => (
  { dash: '仪表盘', users: '用户管理', sessions: '会话记录', settings: '模型设置' }[view.value]))

const statCards = ref<{ label: string; num: number; bg: string; color: string }[]>([])
const recentUsers = ref<any[]>([])

async function doGateLogin() {
  gErr.value = ''
  try {
    const u = await auth.login(gUser.value.trim(), gPass.value)
    if (u.role !== 'admin') { gErr.value = '该账户不是管理员'; auth.logout(); return }
    enter()
  } catch (e: any) { gErr.value = e.message }
}
function enter() {
  entered.value = true
  loadDash(); loadUsers(); loadSessions()
}
function doLogout() {
  auth.logout()
  gPass.value = ''
  entered.value = false
  gateMsg.value = '已退出登录，请重新登录'
}
function onNav(key: string) {
  if (key === 'chat') { router.push('/'); return }
  view.value = key as any
  if (key === 'settings' && !lcLoaded.value) { lcLoaded.value = true; loadLLM() }
}

async function loadDash() {
  try {
    const s = await api<any>('/api/admin/stats')
    statCards.value = [
      { label: '👥 用户总数', num: s.users_total, bg: '#e6f2ef', color: '#0f6f5c' },
      { label: '✨ 今日新增', num: s.users_today, bg: '#fdf1dd', color: '#a06a10' },
      { label: '💬 会话总数', num: s.sessions_total, bg: '#eef4ff', color: '#3f6ac6' },
      { label: '✉️ 消息总数', num: s.messages_total, bg: '#f3e9f7', color: '#7c4d9e' },
    ]
    const d = await api<any>('/api/admin/users?size=5')
    recentUsers.value = d.items
  } catch (e: any) { ElMessage.error(e.message) }
}

/* 用户管理 */
const uSearch = ref(''); const uPage = ref(1); const uSize = 10
const uRows = ref<any[]>([]); const uTotal = ref(0)
async function loadUsers() {
  try {
    const d = await api<any>(`/api/admin/users?search=${encodeURIComponent(uSearch.value)}&page=${uPage.value}&size=${uSize}`)
    uRows.value = d.items; uTotal.value = d.total
  } catch (e: any) { ElMessage.error(e.message) }
}

const umShow = ref(false)
const editingId = ref<number | null>(null)
const um = ref({ username: '', password: '', nickname: '', role: 'user', phone: '', active: true })
function openCreate() {
  editingId.value = null
  um.value = { username: '', password: '', nickname: '', role: 'user', phone: '', active: true }
  umShow.value = true
}
function openEdit(row: any) {
  editingId.value = row.id
  um.value = {
    username: row.username, password: '', nickname: row.nickname || '',
    role: row.role, phone: row.phone || '', active: !!row.is_active,
  }
  umShow.value = true
}
async function saveUser() {
  try {
    if (editingId.value == null) {
      await api('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username: um.value.username.trim(), password: um.value.password,
          nickname: um.value.nickname.trim(), role: um.value.role,
          phone: um.value.phone.trim() || null,
        }),
      })
    } else {
      await api(`/api/admin/users/${editingId.value}`, {
        method: 'PUT',
        body: JSON.stringify({
          nickname: um.value.nickname.trim(), role: um.value.role,
          phone: um.value.phone.trim() || null, is_active: um.value.active,
        }),
      })
    }
    umShow.value = false
    loadUsers(); loadDash()
  } catch (e: any) { ElMessage.error(e.message) }
}
async function resetPwd(row: any) {
  try {
    const { value } = await ElMessageBox.prompt(`为用户「${row.username}」设置新密码（至少6位）：`, '重置密码', {
      inputType: 'password', inputPattern: /^.{6,}$/, inputErrorMessage: '密码至少 6 位',
    })
    await api(`/api/admin/users/${row.id}`, { method: 'PUT', body: JSON.stringify({ new_password: value }) })
    ElMessage.success('密码已重置')
  } catch { /* 取消 */ }
}
async function delUser(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除用户「${row.username}」？该操作不可恢复（其历史会话保留）。`, '删除确认', { type: 'warning' })
    await api(`/api/admin/users/${row.id}`, { method: 'DELETE' })
    loadUsers(); loadDash()
  } catch (e: any) {
    if (e !== 'cancel' && (e as any)?.message !== 'cancel') ElMessage.error(e.message || String(e))
  }
}

/* 会话记录 */
const sSearch = ref(''); const sPage = ref(1); const sSize = 10
const sRows = ref<any[]>([]); const sTotal = ref(0)
async function loadSessions() {
  try {
    const d = await api<any>(`/api/admin/sessions?search=${encodeURIComponent(sSearch.value)}&page=${sPage.value}&size=${sSize}`)
    sRows.value = d.items; sTotal.value = d.total
  } catch (e: any) { ElMessage.error(e.message) }
}
const cmShow = ref(false); const cmSid = ref(''); const cmItems = ref<any[]>([])
async function viewSess(sid: string) {
  cmSid.value = sid
  cmItems.value = (await api<any>(`/api/admin/sessions/${sid}/messages`)).items
  cmShow.value = true
}

/* 模型设置（运行时热切换 LLM 配置） */
const lcLoaded = ref(false)
const lc = ref<any>({ base_url: '', api_key: '', model: '', fast_model: '', thinking: 'high', temperature: 0.6, persisted: true })
const lcApiKeyMasked = ref('')
const lcSaving = ref(false)
const lcTesting = ref(false)

async function loadLLM() {
  try {
    const d = await api<any>('/api/admin/llm-config')
    lcApiKeyMasked.value = d.api_key || ''
    lc.value = {
      base_url: d.base_url || '', api_key: '', model: d.model || '',
      fast_model: d.fast_model || '', thinking: d.thinking || 'high',
      temperature: Number(d.temperature) || 0.6, persisted: !!d.persisted,
    }
  } catch (e: any) { ElMessage.error(e.message) }
}

async function saveLLM() {
  lcSaving.value = true
  try {
    const body: any = {
      base_url: lc.value.base_url.trim(),
      model: lc.value.model.trim(),
      fast_model: lc.value.fast_model.trim(), // 空串 = 回退主模型
      thinking: lc.value.thinking,
      temperature: lc.value.temperature,
    }
    if (lc.value.api_key.trim()) body.api_key = lc.value.api_key.trim()
    const d = await api<any>('/api/admin/llm-config', { method: 'PUT', body: JSON.stringify(body) })
    lcApiKeyMasked.value = d.api_key || ''
    lc.value.api_key = ''
    lc.value.persisted = !!d.persisted
    ElMessage.success('模型配置已保存并即时生效')
  } catch (e: any) { ElMessage.error(e.message) }
  finally { lcSaving.value = false }
}

async function testLLM() {
  lcTesting.value = true
  try {
    const d = await api<any>('/api/admin/llm-config/test', { method: 'POST' })
    if (d.ok) ElMessage.success(`连接正常：${d.model}，延迟 ${d.latency_ms}ms`)
    else ElMessage.error(`连接失败：${d.error}（${d.latency_ms}ms）`)
  } catch (e: any) { ElMessage.error(e.message) }
  finally { lcTesting.value = false }
}

onMounted(async () => {
  await auth.refresh()
  if (auth.isAdmin) { enter(); return }
  if (auth.isLoggedIn) gateMsg.value = '账户 ' + auth.user!.username + ' 不是管理员'
})
</script>

<style scoped>
.admin-page { height: 100% }
.gate { max-width: 420px; margin: 80px auto; background: #fff; border: 1px solid var(--border); border-radius: 14px; padding: 30px }
.gate h2 { margin-bottom: 8px; text-align: center }
.gate p { color: var(--muted); font-size: 13px; margin-bottom: 16px; text-align: center }
.gate-links { margin-top: 14px; text-align: center; font-size: 13px }
.gate-links a { color: var(--primary); text-decoration: none; cursor: pointer }
.console { height: 100% }
.side { background: #1d2b33; display: flex; flex-direction: column }
.side :deep(.el-menu) { border-right: none; flex: 1 }
.brand { padding: 20px 18px; border-bottom: 1px solid rgba(255,255,255,.08) }
.brand h2 { font-size: 16px; color: #fff }
.brand p { font-size: 11px; color: #7d8f99; margin-top: 4px }
.foot { padding: 14px 18px; border-top: 1px solid rgba(255,255,255,.08); font-size: 12px; color: #7d8f99 }
.foot a { color: #9fc3b8; cursor: pointer; text-decoration: none }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px }
.topbar h1 { font-size: 19px }
.topbar .who { font-size: 13px; color: var(--muted) }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-bottom: 20px }
.card { background: #fff; border: 1px solid var(--border); border-radius: 12px; padding: 18px }
.card .num { font-size: 26px; font-weight: 700; margin-top: 6px }
.card .lbl { font-size: 13px; color: var(--muted) }
.card .ico { width: 34px; height: 34px; border-radius: 9px; display: flex; align-items: center; justify-content: center; font-size: 17px }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; align-items: center; flex-wrap: wrap }
.flex1 { flex: 1 }
.mono { font-family: monospace; font-size: 12px }
.chat-view { max-height: 340px; overflow: auto; border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: #fbfcfd }
.cv-msg { margin-bottom: 10px; font-size: 13px; line-height: 1.6 }
.cv-msg b { color: var(--primary) }
.muted { color: var(--muted); font-size: 13px }
.hint { font-size: 12px; color: var(--muted); margin-top: 4px; width: 100% }
.lc-actions { display: flex; gap: 10px; align-items: center; margin-top: 4px }
</style>
