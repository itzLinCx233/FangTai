<template>
  <div class="chat-page">
    <!-- 左侧边栏：品牌 / 新对话 / 历史记录 / 账号 -->
    <aside class="side" :class="{ collapsed: sideCollapsed }">
      <div class="side-top">
        <div class="brand">
          <span class="s-logo">🍲</span>
          <span class="s-name" v-show="!sideCollapsed">个性化膳食规划 Agent</span>
        </div>
        <button class="icon-btn" :title="sideCollapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleSide">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <rect x="1.5" y="2.5" width="13" height="11" rx="2.5" stroke="currentColor" stroke-width="1.4"/>
            <line x1="10.5" y1="2.5" x2="10.5" y2="13.5" stroke="currentColor" stroke-width="1.4"/>
          </svg>
        </button>
      </div>
      <button class="new-chat" title="开启新对话" @click="newChat">
        <span class="plus">＋</span><span v-show="!sideCollapsed">开启新对话</span>
      </button>
      <div class="hist" v-show="!sideCollapsed">
        <template v-for="[label, arr] in groupedSessions" :key="label">
          <div class="g-title">{{ label }}</div>
          <div class="h-item" v-for="s in arr" :key="s.session_id" :class="{ active: sessionId === s.session_id }"
               :title="s.title" @click="openHistory(s)">{{ s.title || '（未命名会话）' }}</div>
        </template>
        <div v-if="auth.user && groupedSessions.length === 0" class="h-empty">暂无历史对话</div>
      </div>
      <div class="side-bottom">
        <div class="acct" @click="menuOpen = !menuOpen">
          <div class="avatar" :class="{ admin: auth.isAdmin }">{{ avatarChar }}</div>
          <span class="a-name" v-show="!sideCollapsed">{{ auth.user!.nickname || auth.user!.username }}</span>
          <span class="dots" v-show="!sideCollapsed">···</span>
        </div>
        <div class="user-menu" v-show="menuOpen">
          <a @click="openCenter">个人中心</a>
          <a v-if="auth.isAdmin" @click="goAdmin">管理后台</a>
          <div class="sep"></div>
          <a class="logout" @click="doLogout">退出登录</a>
        </div>
      </div>
    </aside>

    <!-- 主区：消息 + 输入 -->
    <div class="main-col">
    <!-- 消息区 -->
    <main ref="chatMain">
      <div class="welcome" v-if="items.length === 0">
        <h2>您好，我是您的健康膳食助手 🥗</h2>
        <p>告诉我吃什么、几个人吃、有什么忌口，我来为您规划一餐<br>可在个人中心设置同餐人健康档案，推荐自动规避过敏原与慢病忌口</p>
      </div>
      <template v-for="(it, i) in items" :key="i">
        <div v-if="it.type === 'msg'" class="msg" :class="it.role">
          <div class="avatar-c">{{ it.role === 'user' ? '🧑' : '🥗' }}</div>
          <div class="bubble" :class="{ typing: it.text === '' && !it.done }">{{ it.text || dots }}</div>
        </div>
        <div v-else-if="it.type === 'system'" class="msg ai">
          <div class="avatar-c">🥗</div>
          <div class="bubble system">{{ it.text }}</div>
        </div>
        <div v-else-if="it.type === 'plan'" class="plan-card">
          <h4>📋 本餐方案 · 忌口：{{ (it.plan.taboos || []).join('、') || '无' }}</h4>
          <div class="dishes">
            <div class="dish" v-for="d in sortDishes(it.plan.dishes)" :key="d.id"
                 :class="[{ link: d.detail }, roleClass(d.role)]" @click="openDish(d)">
              <div class="name">{{ d.name }}<span v-if="d.kept" class="kept">沿用</span></div>
              <div class="reason">{{ d.reason || '推荐理由生成中…' }}</div>
            </div>
          </div>
        </div>
      </template>
    </main>

    <!-- 输入区 -->
    <footer>
      <div class="quick">
        <button v-for="q in quicks" :key="q.q" @click="input = q.q; send()">{{ q.t }}</button>
      </div>
      <div class="input-row">
        <textarea v-model="input" placeholder="例如：家里就剩番茄、鸡蛋和土豆，怎么弄一顿正餐？"
          @keydown.enter.exact.prevent="send"></textarea>
        <button class="send" :disabled="busy" @click="send">发送</button>
      </div>
      <div class="stats">{{ statsLine }}</div>
    </footer>
    </div><!-- /main-col -->

    <!-- 个人中心模态框 -->
    <div class="modal-mask" v-if="ucMask" @click.self="ucMask = false">
      <div class="modal wide">
        <div class="modal-head"><h3>个人中心</h3><button class="close-x" @click="ucMask = false">×</button></div>
        <div class="modal-body" v-if="auth.user">
          <div class="uc-section"><h4>📊 账号信息</h4>
            <div class="uc-info">账号：<b>{{ auth.user.username }}</b> ｜ 角色：{{ auth.user.role === 'admin' ? '管理员' : '普通用户' }}<br>
              性别：{{ auth.user.gender || '保密' }} ｜ 生日：{{ auth.user.birthday || '保密' }} ｜
              身高：{{ auth.user.height_cm ? auth.user.height_cm + 'cm' : '保密' }} ｜
              体重：{{ auth.user.weight_kg ? auth.user.weight_kg + 'kg' : '保密' }}<br>
              忌口：{{ auth.user.taboo || '无' }} ｜ 居住地：{{ auth.user.city || '保密' }} ｜ 注册时间：{{ auth.user.created_at }}</div>
          </div>
          <div class="uc-section"><h4>👤 资料设置</h4>
            <div class="form-item"><label>昵称</label><input v-model="uc.nick"></div>
            <div class="form-item"><label>性别</label>
              <div class="radio-row">
                <label v-for="g in ['男', '女', '保密']" :key="g" class="radio-item">
                  <input type="radio" :value="g" v-model="uc.gender">{{ g }}
                </label>
              </div>
            </div>
            <div class="form-item"><label>生日</label>
              <input type="date" v-model="uc.birthday" max="today-max">
              <div class="hint">用于推算年龄（0-100 岁）；不填视为保密</div>
            </div>
            <div class="form-item"><label>忌口</label>
              <input type="text" v-model="uc.taboo" maxlength="100" placeholder="如：海鲜、辣、香菜">
              <div class="hint">多个用「、」隔开，不填视为无；推荐时自动规避</div>
            </div>
            <div class="form-item two-col">
              <div><label>身高（cm，可选）</label><input type="number" v-model.number="uc.height_cm" min="1" max="250" placeholder="如 175"></div>
              <div><label>体重（kg，可选）</label><input type="number" v-model.number="uc.weight_kg" min="1" max="300" placeholder="如 65"></div>
            </div>
            <div class="form-item"><label>居住地（可选）</label>
              <input type="text" v-model="uc.city" maxlength="32" placeholder="如：浙江杭州">
              <div class="hint">不填视为保密</div>
            </div>
            <div class="form-item"><label>同餐人档案（多人宴请，可多选）</label>
              <select v-model="uc.companions" multiple>
                <option v-for="p in profiles" :key="p.id" :value="p.id">{{ profileLabel(p) }}</option>
              </select>
              <div class="hint">推荐将同时满足所选档案的过敏/忌口/健康需求约束</div>
            </div>
            <button class="btn primary" @click="saveProfile">保存资料</button>
          </div>
          <div class="uc-section"><h4>🔒 修改密码</h4>
            <div class="form-item"><label>原密码</label><input v-model="pw.old" type="password"></div>
            <div class="form-item"><label>新密码</label><input v-model="pw.new" type="password" placeholder="至少6位"></div>
            <button class="btn" @click="changePassword">修改密码</button>
          </div>
          <div class="uc-section"><h4>🕘 历史会话</h4>
            <div class="sess-list">
              <div v-if="ucSessions.length === 0" class="muted">暂无历史会话，去聊一餐吧～</div>
              <div v-for="s in ucSessions" :key="s.session_id" class="sess-item" @click="loadSession(s.session_id)">
                <div><div class="t">{{ s.title || '（未命名会话）' }}</div>
                  <div class="m">{{ s.created_at }} · {{ s.msg_count }} 条消息</div></div>
                <div class="muted">›</div>
              </div>
            </div>
            <div v-if="sessionView.length" class="chat-view">
              <div v-for="(m, i) in sessionView" :key="i" class="cv-msg"><b>{{ m.role === 'user' ? '我' : '助手' }}：</b>{{ m.content }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

    <!-- 菜品详情弹窗：菜名 / 分割线 / 详细配料表 / 分割线 / 详细做法 -->
    <div class="modal-mask" v-if="dishView" @click.self="dishView = null">
      <div class="modal dish-modal">
        <button class="close-x dm-close" @click="dishView = null">×</button>
        <div class="modal-body dm-body">
          <h3 class="dm-name">{{ dishView.name }}</h3>
          <hr class="dm-hr">
          <h4 class="dm-sub">详细配料表</h4>
          <ul class="dm-ings">
            <li v-for="(g, k) in dishView.detail?.ingredients || []" :key="k">{{ g }}</li>
          </ul>
          <hr class="dm-hr">
          <h4 class="dm-sub">详细做法</h4>
          <ol class="dm-steps">
            <li v-for="(s, k) in dishView.detail?.steps || []" :key="k">{{ s }}</li>
          </ol>
        </div>
      </div>
    </div>

    <!-- 首次登录：完善个人信息（登录后过渡进入） -->
    <Transition name="ob">
      <div class="modal-mask ob-mask" v-if="obShow">
        <div class="modal">
          <div class="modal-head"><h3>完善个人信息</h3></div>
          <div class="modal-body">
            <p class="ob-tip">初次见面！简单介绍一下自己，推荐会更懂你（生日与性别用于营养目标测算）</p>
            <div class="err">{{ obErr }}</div>
            <div class="form-item">
              <label>性别 <span class="req">*</span></label>
              <div class="radio-row">
                <label v-for="g in ['男', '女', '保密']" :key="g" class="radio-item">
                  <input type="radio" :value="g" v-model="ob.gender">{{ g }}
                </label>
              </div>
            </div>
            <div class="form-item">
              <label>生日 <span class="req">*</span></label>
              <input type="date" v-model="ob.birthday">
              <div class="hint">年龄须在 0-100 岁之间{{ obAge !== null ? '（当前 ' + obAge + ' 岁）' : '' }}</div>
            </div>
            <div class="form-item">
              <label>忌口（可选）</label>
              <input type="text" v-model="ob.taboo" maxlength="100" placeholder="如：海鲜、辣、香菜">
              <div class="hint">多个用「、」隔开，不填视为无；推荐时自动规避</div>
            </div>
            <div class="form-item two-col">
              <div><label>身高（cm，可选）</label><input type="number" v-model.number="ob.height_cm" min="1" max="250" placeholder="如 175"></div>
              <div><label>体重（kg，可选）</label><input type="number" v-model.number="ob.weight_kg" min="1" max="300" placeholder="如 65"></div>
            </div>
            <div class="form-item">
              <label>居住地（可选）</label>
              <input type="text" v-model="ob.city" maxlength="32" placeholder="如：浙江杭州">
              <div class="hint">不填视为保密</div>
            </div>
            <div class="ob-actions">
              <button class="btn" @click="skipOnboard">暂时跳过</button>
              <button class="btn primary" :disabled="obLoading" @click="finishOnboard">{{ obLoading ? '保存中…' : '完成' }}</button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api, getToken, profileLabel, type Profile } from '../api'
import { useAuth } from '../stores/auth'

const router = useRouter()
const auth = useAuth()

type Item =
  | { type: 'msg'; role: 'user' | 'ai'; text: string; done?: boolean }
  | { type: 'system'; text: string }
  | { type: 'plan'; plan: any }

const items = ref<Item[]>([])
const input = ref('')
const busy = ref(false)
const sessionId = ref<string | null>(null)
const statsLine = ref('')
const chatMain = ref<HTMLElement>()
const menuOpen = ref(false)
const profiles = ref<Profile[]>([])
const companions = ref<number[]>(JSON.parse(localStorage.getItem('ft_companions') || '[]'))
const quicks = [
  { t: '今晚吃啥', q: '今晚吃啥比较好？' },
  { t: '两人减脂晚餐', q: '帮我安排一顿两人的晚餐，最近在减脂' },
  { t: '四菜一汤家宴', q: '做个四菜一汤，小孩不吃辣，老人牙口不好' },
  { t: '半小时晚饭', q: '我今晚下班晚，想做个半小时内能搞定的晚饭' },
]
const dots = '…'

/* 菜品详情弹窗 */
const dishView = ref<any>(null)
function openDish(d: any) { if (d?.detail) dishView.value = d }

/* 双列对称展示，汤固定排在左上角首位（仅显示顺序，不影响 plan 事件本身） */
function sortDishes(ds: any[]) {
  if (!Array.isArray(ds)) return ds || []
  const isSoup = (d: any) => d.role === '汤' || /汤/.test(d.name || '')
  return [...ds.filter(isSoup), ...ds.filter((d) => !isSoup(d))]
}

/* 按角色分底色：汤黄 / 素绿 / 荤淡红，其余默认 */
function roleClass(role: string) {
  if (role === '汤') return 'soup'
  if (['素菜', '豆制品', '蛋类'].includes(role)) return 'veg'
  if (['荤菜', '主菜'].includes(role)) return 'meat'
  return ''
}

/* 选菜 LLM 偶发漏写理由：其他菜先显示，空理由菜异步重试生成 */
async function fillEmptyReasons(plan: any) {
  const empties = (plan.dishes || []).filter((d: any) => !d.reason)
  if (!empties.length) return
  await Promise.all(empties.map(async (d: any) => {
    try {
      const r = await api<{ reason: string }>('/api/dish_reason', {
        method: 'POST',
        body: JSON.stringify({ dish_id: d.id, session_id: sessionId.value }),
      })
      if (r.reason) d.reason = r.reason
      else d.reason = '与本餐荤素搭配协调，营养均衡'
    } catch {
      d.reason = '与本餐荤素搭配协调，营养均衡'
    }
  }))
}

const avatarChar = computed(() => {
  const u = auth.user
  return ((u!.nickname || u!.username)[0] || '?').toUpperCase()
})

function pushSystem(text: string) { items.value.push({ type: 'system', text }) }
function scrollBottom() { nextTick(() => { if (chatMain.value) chatMain.value.scrollTop = chatMain.value.scrollHeight }) }

function currentProfileIds(): number[] {
  const ids: number[] = []
  if (auth.user?.health_profile_id) ids.push(auth.user.health_profile_id)
  for (const pid of companions.value) if (!ids.includes(pid)) ids.push(pid)
  return ids
}

/* ---------- 发送与 SSE 流式（与旧版逻辑一致） ---------- */
async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  const isNewChat = sessionId.value === null
  busy.value = true
  input.value = ''
  items.value.push({ type: 'msg', role: 'user', text, done: true })
  items.value.push({ type: 'msg', role: 'ai', text: '', done: false })
  const aiIdx = items.value.length - 1
  const aiObj: any = items.value[aiIdx]   // 响应式代理：直接改原始对象不会触发渲染
  const t0 = performance.now()
  let firstTok: number | null = null
  let hasPlan = false
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' },
        getToken() ? { Authorization: 'Bearer ' + getToken() } : {}),
      body: JSON.stringify({ message: text, session_id: sessionId.value, profile_ids: currentProfileIds() }),
    })
    sessionId.value = resp.headers.get('X-Session-Id') || sessionId.value
    const reader = resp.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      while (buf.includes('\n\n')) {
        const raw = buf.slice(0, buf.indexOf('\n\n'))
        buf = buf.slice(buf.indexOf('\n\n') + 2)
        if (!raw.startsWith('data: ')) continue
        let ev: any
        try { ev = JSON.parse(raw.slice(6)) } catch { continue }
        if (ev.type === 'plan') {
          // 方案卡片即本轮回复：出卡即移除文字气泡（前导句只是选菜期间的过渡显示）
          hasPlan = true
          items.value.push({ type: 'plan', plan: ev })
          const i = items.value.indexOf(aiObj)
          if (i >= 0) items.value.splice(i, 1)
          // 必须取 items 内的响应式代理再改 reason，否则视图不刷新（占位会一直挂着）
          fillEmptyReasons((items.value[items.value.length - 1] as any).plan)
        } else if (ev.type === 'delta') {
          if (firstTok === null) firstTok = performance.now() - t0
          if (!hasPlan) aiObj.text += ev.text
        } else if (ev.type === 'clarify') {
          // 澄清问题随后会以 delta 流式下发，这里不重复拼接
        } else if (ev.type === 'done') {
          statsLine.value = `首Token ${firstTok ? (firstTok / 1000).toFixed(2) : '-'}s · 端到端 ${((performance.now() - t0) / 1000).toFixed(2)}s`
        }
        scrollBottom()
      }
    }
    if (hasPlan) {
      const i = items.value.indexOf(aiObj)
      if (i >= 0) items.value.splice(i, 1)
    }
    aiObj.done = true
  } catch (e: any) {
    aiObj.text = '出错了：' + e.message
    aiObj.done = true
  } finally { busy.value = false; scrollBottom(); if (isNewChat) loadSessions() }
}

const OPENING_TEXT = '您好！很高兴为您服务 🥗\n开始规划之前，先了解一下您的需求：\n1. 想吃哪一餐？（早餐 / 午餐 / 晚餐 / 夜宵）\n2. 几个人吃？\n3. 有忌口或过敏吗？（如海鲜、辣、花生…）\n4. 其他要求也可以告诉我，比如：想减脂增肌、半小时内搞定、家里只剩番茄鸡蛋土豆等食材'

/* AI 主动发起开场对话（打字机效果；经 reactive 代理赋值确保逐字渲染） */
function aiOpening() {
  items.value.push({ type: 'msg', role: 'ai', text: '', done: false })
  const idx = items.value.length - 1
  const total = OPENING_TEXT.length
  const t0 = Date.now()
  const timer = setInterval(() => {
    const cur: any = items.value[idx]
    if (!cur) { clearInterval(timer); return }
    // 按已流逝时间计算应显示的字符数，标签页被节流也能追上进度
    const n = Math.min(total, Math.ceil((Date.now() - t0) / 18))
    cur.text = OPENING_TEXT.slice(0, n)
    scrollBottom()
    if (n >= total) { cur.done = true; clearInterval(timer) }
  }, 40)
}

function newChat() {
  sessionId.value = null; items.value = []; statsLine.value = ''
  aiOpening()
}

function doLogout() {
  menuOpen.value = false
  auth.logout()
  router.push('/login')
}
function goAdmin() { menuOpen.value = false; router.push('/admin') }

/* ---------- 个人中心 ---------- */
const ucMask = ref(false)
const uc = ref({ nick: '', companions: [] as number[], gender: '保密', birthday: '', taboo: '', city: '', height_cm: null as number | null, weight_kg: null as number | null })
const pw = ref({ old: '', new: '' })
const ucSessions = ref<any[]>([])
const sessionView = ref<any[]>([])

/* ---------- 左侧边栏 ---------- */
const sideCollapsed = ref(localStorage.getItem('ft_side_collapsed') === '1')
function toggleSide() {
  sideCollapsed.value = !sideCollapsed.value
  localStorage.setItem('ft_side_collapsed', sideCollapsed.value ? '1' : '0')
}
const groupedSessions = computed<[string, any[]][]>(() => {
  const buckets: Record<string, any[]> = { '7 天内': [], '30 天内': [], '更早': [] }
  const now = Date.now()
  for (const s of ucSessions.value) {
    const t = new Date(String(s.created_at || '').replace(' ', 'T')).getTime()
    const days = (now - (isNaN(t) ? now : t)) / 86400000
    if (days <= 7) buckets['7 天内'].push(s)
    else if (days <= 30) buckets['30 天内'].push(s)
    else buckets['更早'].push(s)
  }
  return Object.entries(buckets).filter(([, arr]) => arr.length > 0)
})
async function loadSessions() {
  try { ucSessions.value = (await api<any>('/api/auth/sessions')).items } catch { /* ignore */ }
}
async function openHistory(s: any) {
  menuOpen.value = false
  sessionId.value = s.session_id
  items.value = []
  try {
    const d = await api<any>(`/api/auth/sessions/${s.session_id}/messages`)
    for (const m of d.items) items.value.push({ type: 'msg', role: m.role === 'user' ? 'user' : 'ai', text: m.content, done: true })
  } catch { /* ignore */ }
  scrollBottom()
}

async function openCenter() {
  menuOpen.value = false
  const u = auth.user!
  uc.value.nick = u.nickname || ''
  uc.value.gender = u.gender || '保密'
  uc.value.birthday = u.birthday || ''
  uc.value.taboo = u.taboo || ''
  uc.value.city = u.city || ''
  uc.value.height_cm = u.height_cm || null
  uc.value.weight_kg = u.weight_kg || null
  uc.value.companions = [...companions.value]
  ucSessions.value = []
  sessionView.value = []
  ucMask.value = true
  try { ucSessions.value = (await api<any>('/api/auth/sessions')).items }
  catch { /* ignore */ }
}
async function loadSession(sid: string) {
  sessionView.value = (await api<any>(`/api/auth/sessions/${sid}/messages`)).items
}
async function saveProfile() {
  companions.value = [...uc.value.companions]
  localStorage.setItem('ft_companions', JSON.stringify(companions.value))
  await api('/api/auth/profile', {
    method: 'PUT',
    body: JSON.stringify({
      nickname: uc.value.nick,
      gender: uc.value.gender || '保密',
      birthday: uc.value.birthday || '',
      taboo: uc.value.taboo,
      city: uc.value.city,
      height_cm: uc.value.height_cm || null,
      weight_kg: uc.value.weight_kg || null,
    }),
  })
  await auth.refresh()
  ucMask.value = false
  pushSystem('资料已保存' + (companions.value.length ? `，同餐人约束：${companions.value.join('、')} 号档案` : ''))
}
async function changePassword() {
  try {
    await api('/api/auth/profile', {
      method: 'PUT',
      body: JSON.stringify({ old_password: pw.value.old, new_password: pw.value.new }),
    })
    pw.value = { old: '', new: '' }
    alert('密码修改成功')
  } catch (e: any) { alert('修改失败：' + e.message) }
}


/* ---------- 首次登录信息完善 ---------- */
const obShow = ref(false)
const obLoading = ref(false)
const obErr = ref('')
const ob = ref({ gender: '', birthday: '', taboo: '', city: '', height_cm: null as number | null, weight_kg: null as number | null })

const obAge = computed<number | null>(() => {
  if (!ob.value.birthday) return null
  const d = new Date(ob.value.birthday)
  if (isNaN(d.getTime())) return null
  const now = new Date()
  let age = now.getFullYear() - d.getFullYear()
  const m = now.getMonth() - d.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--
  return age
})

function profileIncomplete(): boolean {
  const u = auth.user
  return !!u && (!u.gender || !u.birthday)
}

function obDismissKey() {
  return 'ft_ob_dismissed_' + (auth.user?.username || '')
}

function maybeShowOnboard() {
  if (!profileIncomplete()) return
  // 跳过按账号持久化（localStorage）：刷新/重开不再反复弹；仍可从个人中心补填
  if (localStorage.getItem(obDismissKey())) return
  ob.value = { gender: auth.user!.gender || '', birthday: auth.user!.birthday || '',
               taboo: auth.user!.taboo || '', city: auth.user!.city || '',
               height_cm: auth.user!.height_cm || null, weight_kg: auth.user!.weight_kg || null }
  obShow.value = true
}

function skipOnboard() {
  localStorage.setItem(obDismissKey(), '1')
  obShow.value = false
}

async function finishOnboard() {
  obErr.value = ''
  if (!ob.value.gender) { obErr.value = '请选择性别'; return }
  if (!ob.value.birthday) { obErr.value = '请填写生日'; return }
  if (obAge.value === null || obAge.value < 0 || obAge.value > 100) {
    obErr.value = '由生日推算的年龄须在 0-100 岁之间'; return
  }
  obLoading.value = true
  try {
    await api('/api/auth/profile', {
      method: 'PUT',
      body: JSON.stringify({
        gender: ob.value.gender,
        birthday: ob.value.birthday,
        taboo: ob.value.taboo,
        city: ob.value.city,
        height_cm: ob.value.height_cm || null,
        weight_kg: ob.value.weight_kg || null,
      }),
    })
    await auth.refresh()
    obShow.value = false
    pushSystem('个人信息已完善' + (ob.value.taboo.trim() ? '，忌口（' + ob.value.taboo.trim() + '）将在推荐中自动规避' : ''))
  } catch (e: any) { obErr.value = e.message }
  finally { obLoading.value = false }
}

onMounted(async () => {
  await auth.refresh()
  maybeShowOnboard()
  loadSessions()
  try {
    const d = await fetch('/api/profiles?kind=simple').then(r => r.json())
    profiles.value = d.items
  } catch { /* ignore */ }
})
</script>

<style scoped>
.chat-page { height: 100%; display: flex; flex-direction: row }

/* ---------- 左侧边栏（深色，参考 DeepSeek 风格） ---------- */
.side {
  width: 264px; flex-shrink: 0; display: flex; flex-direction: column;
  background: #1f2529; transition: width .22s ease; overflow: hidden;
}
.side.collapsed { width: 64px }
.side-top { display: flex; align-items: center; justify-content: space-between; padding: 14px 12px 8px }
.side.collapsed .side-top { flex-direction: column; gap: 10px; padding: 14px 8px 8px }
.brand { display: flex; align-items: center; gap: 10px; min-width: 0 }
.s-logo { width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0; background: linear-gradient(135deg, var(--primary), #1a9b7e); display: flex; align-items: center; justify-content: center; font-size: 18px }
.s-name { color: #f0f3f5; font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.icon-btn { color: #9aa3ab; background: none; border: none; cursor: pointer; padding: 6px; border-radius: 8px; display: flex; align-items: center; flex-shrink: 0 }
.icon-btn:hover { background: rgba(255, 255, 255, .08); color: #fff }
.new-chat {
  margin: 8px 12px 10px; padding: 10px 14px; border: none; border-radius: 999px;
  background: #343c43; color: #f0f3f5; font-size: 13.5px; cursor: pointer;
  display: flex; align-items: center; gap: 8px; justify-content: center;
  transition: background .15s ease;
}
.new-chat:hover { background: #3f4850 }
.new-chat .plus { font-size: 15px; line-height: 1 }
.side.collapsed .new-chat { margin: 8px auto 10px; width: 40px; height: 40px; padding: 0; border-radius: 50% }
.hist { flex: 1; overflow-y: auto; padding: 0 10px 8px }
.hist::-webkit-scrollbar { width: 6px }
.hist::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, .12); border-radius: 3px }
.g-title { color: #8b949c; font-size: 12px; padding: 12px 8px 6px }
.h-item {
  color: #c8cdd3; font-size: 13.5px; padding: 8px 10px; border-radius: 8px; cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.h-item:hover { background: rgba(255, 255, 255, .07) }
.h-item.active { background: rgba(255, 255, 255, .1); color: #fff }
.h-empty { color: #7d868e; font-size: 12.5px; text-align: center; padding: 22px 0 }
.side-bottom { position: relative; padding: 10px 10px 14px }
.acct { display: flex; align-items: center; gap: 10px; padding: 7px 8px; border-radius: 10px; cursor: pointer }
.acct:hover { background: rgba(255, 255, 255, .06) }
.side.collapsed .acct { justify-content: center; padding: 7px 0 }
.a-name { color: #e8ecef; font-size: 13px; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.dots { color: #9aa3ab; font-size: 13px; letter-spacing: 1px }
.side .user-menu { top: auto; bottom: 56px; left: 10px; right: 10px; min-width: 0 }

/* ---------- 主区 ---------- */
.main-col { flex: 1; min-width: 0; display: flex; flex-direction: column }
main { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 18px }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: linear-gradient(135deg, #5470c6, #7a90da); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; cursor: pointer; user-select: none; flex-shrink: 0 }
.avatar.admin { background: linear-gradient(135deg, var(--accent), #f0b95f) }
.user-menu { position: absolute; background: #fff; border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.18); min-width: 150px; padding: 6px; z-index: 30 }
.user-menu a { display: block; padding: 9px 12px; border-radius: 7px; font-size: 13px; cursor: pointer; color: var(--text) }
.user-menu a:hover { background: var(--primary-light); color: var(--primary) }
.user-menu .logout { color: var(--danger) }
.user-menu .sep { height: 1px; background: var(--border); margin: 4px 6px }
.msg { display: flex; gap: 12px; max-width: 860px; width: 100%; margin: 0 auto }
.msg .avatar-c { width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 15px; color: #fff; background: var(--primary) }
.msg.user .avatar-c { background: #5470c6 }
.bubble { padding: 12px 16px; border-radius: 12px; line-height: 1.7; font-size: 14.5px; white-space: pre-wrap; word-break: break-word; max-width: calc(100% - 60px); background: var(--card); border: 1px solid var(--border) }
.msg.user .bubble { background: var(--primary); color: #fff; border: none }
.bubble.system { background: #f0f7f5; border-color: #cfe5df; color: #3c6b5d; font-size: 13px }
.bubble.typing::after { content: '●●●'; color: var(--muted); animation: blink 1.2s infinite; letter-spacing: 2px; font-size: 10px }
@keyframes blink { 0%, 80%, 100% { opacity: .25 } 40% { opacity: 1 } }
.plan-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; max-width: 760px; width: 100%; margin: 0 auto }
.plan-card h4 { font-size: 17px; color: var(--primary); margin-bottom: 10px }
.dishes { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px }
.dish { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: #fbfcfd }
.dish.soup { background: #fff8e1; border-color: #f0e0a8 }
.dish.veg { background: #eef7ee; border-color: #c9e6ca }
.dish.meat { background: #fdeeed; border-color: #f2cdc9 }
.dish .name { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap }
.dish .kept { font-size: 11px; background: #eef4ff; color: #3f6ac6; padding: 1px 8px; border-radius: 10px }
.dish .reason { font-size: 12.5px; color: #4a5361; margin-top: 5px; line-height: 1.6 }
.dish.link { cursor: pointer; transition: border-color .15s, box-shadow .15s }
.dish.link:hover { border-color: var(--primary); box-shadow: 0 2px 10px rgba(0,0,0,.06) }

/* 菜品详情弹窗（近正方形大窗：菜名/配料/做法）；.modal.dish-modal 提高特异性压过 .modal{width:440px} */
.modal.dish-modal { width: min(560px, 92vw); height: min(78vh, 800px); display: flex; flex-direction: column; position: relative; overflow: hidden }
.close-x.dm-close { position: absolute; top: 8px; right: 12px; z-index: 3; font-size: 30px; width: 40px; height: 40px; line-height: 40px; border-radius: 50%; transition: background .15s }
.close-x.dm-close:hover { background: #f0f2f5 }
.dm-body { flex: 1; overflow-y: auto; padding: 24px 26px }
.dm-name { font-size: 22px; font-weight: 700; color: var(--text); padding-right: 40px }
.dm-hr { border: none; border-top: 1px solid var(--border); margin: 14px 0 }
.dm-sub { font-size: 17px; color: var(--primary); margin-bottom: 10px }
.dm-ings { list-style: none; display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 18px; margin: 0; padding: 0 }
.dm-ings li { font-size: 15.5px; color: #4a5361; line-height: 1.8 }
.dm-steps { margin: 0; padding-left: 20px }
.dm-steps li { font-size: 15.5px; color: #4a5361; line-height: 1.9; margin-bottom: 8px }
footer { background: var(--card); border-top: 1px solid var(--border); padding: 14px 24px }
.quick { display: flex; gap: 8px; max-width: 860px; margin: 0 auto 8px; flex-wrap: wrap }
.quick button { font-size: 12px; padding: 5px 12px; border: 1px solid var(--border); background: #fff; border-radius: 14px; cursor: pointer; color: #4a5361 }
.quick button:hover { border-color: var(--primary); color: var(--primary) }
.input-row { display: flex; gap: 10px; max-width: 860px; margin: 0 auto }
textarea { flex: 1; padding: 11px 14px; border: 1px solid var(--border); border-radius: 10px; font-size: 14px; font-family: inherit; resize: none; height: 46px; outline: none }
textarea:focus { border-color: var(--primary) }
.send { padding: 0 22px; background: var(--primary); color: #fff; border: none; border-radius: 10px; font-size: 14px; cursor: pointer }
.send:disabled { background: #a8c5bd; cursor: not-allowed }
.stats { font-size: 11px; color: var(--muted); max-width: 860px; margin: 6px auto 0; text-align: right }
.welcome { max-width: 860px; margin: auto; text-align: center; padding: 40px 20px }
.welcome h2 { font-size: 20px; margin-bottom: 8px; color: var(--primary) }
.welcome p { color: var(--muted); font-size: 14px }
/* 模态框 */
.modal-mask { position: fixed; inset: 0; background: rgba(20,26,32,.45); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px }
.modal { background: #fff; border-radius: 14px; width: 440px; max-width: 100%; max-height: 88vh; overflow: auto; box-shadow: 0 20px 60px rgba(0,0,0,.2) }
.modal.wide { width: 640px }
.modal-head { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; background: #fff; z-index: 2 }
.modal-head h3 { font-size: 16px }
.close-x { border: none; background: none; font-size: 20px; color: var(--muted); cursor: pointer }
.modal-body { padding: 20px }
.tabs { display: flex; margin-bottom: 18px; border-bottom: 1px solid var(--border) }
.tabs div { flex: 1; text-align: center; padding: 10px; cursor: pointer; font-size: 14px; color: var(--muted); border-bottom: 2px solid transparent }
.tabs div.on { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600 }
.form-item { margin-bottom: 14px }
.form-item label { display: block; font-size: 13px; color: #4a5361; margin-bottom: 6px }
.form-item input, .form-item select { width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; outline: none; font-family: inherit }
.form-item select[multiple] { height: auto; min-height: 96px }
.form-item .hint { font-size: 12px; color: #6b7480; margin-top: 5px; line-height: 1.5 }
.err { color: var(--danger); font-size: 13px; min-height: 18px; margin-bottom: 8px }
.wide-btn { width: 100%; padding: 10px }
.uc-section { margin-bottom: 20px }
.uc-section h4 { font-size: 14px; margin-bottom: 10px; color: var(--primary) }
.uc-info { font-size: 13px; color: #4a5361; line-height: 1.9 }
.sess-list { display: flex; flex-direction: column; gap: 8px }
.sess-item { border: 1px solid var(--border); border-radius: 10px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; background: #fbfcfd }
.sess-item:hover { border-color: var(--primary) }
.sess-item .t { font-size: 14px; font-weight: 500 }
.sess-item .m { font-size: 12px; color: var(--muted) }
.chat-view { margin-top: 12px; max-height: 300px; overflow: auto; border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: #fbfcfd }
.cv-msg { margin-bottom: 10px; font-size: 13px; line-height: 1.6 }
.cv-msg b { color: var(--primary) }
.muted { color: var(--muted); font-size: 13px }

/* 信息完善/个人中心新样式 */
/* 胶囊选择（性别）：点击整颗切换，选中态主题色 */
.radio-row { display: flex; gap: 10px; flex-wrap: wrap }
.radio-item {
  display: inline-flex; align-items: center; gap: 0;
  padding: 6px 16px; border-radius: 999px;
  border: 1px solid var(--border); background: #fff;
  font-size: 13.5px; cursor: pointer; color: #4a5361;
  user-select: none; transition: all .15s ease; line-height: 1.4;
}
.radio-item:hover { border-color: var(--primary); color: var(--primary) }
.radio-item input { position: absolute; opacity: 0; pointer-events: none; width: 0; height: 0 }
.radio-item:has(input:checked) {
  background: var(--primary); border-color: var(--primary); color: #fff;
  box-shadow: 0 2px 8px rgba(15, 111, 92, .28); font-weight: 500;
}
.radio-item:has(input:focus-visible) { outline: 2px solid var(--primary); outline-offset: 2px }
.two-col { display: flex; gap: 12px }
.two-col > div { flex: 1 }
.two-col input { width: 100% }
.req { color: var(--danger); margin-left: 2px; font-weight: 600 }
.ob-tip { font-size: 13px; color: #5d6873; margin-bottom: 16px; line-height: 1.65; background: #f4f8f6; border-left: 3px solid var(--primary); padding: 8px 12px; border-radius: 0 8px 8px 0 }
.ob-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border) }

/* 登录 → 完善个人信息：过渡动画（遮罩淡入 + 卡片升起 + 内容错峰浮现） */
@keyframes obFadeUp { from { opacity: 0; transform: translateY(14px) } to { opacity: 1; transform: none } }
.ob-mask { background: rgba(10, 20, 17, .5); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px) }
.ob-enter-active { transition: opacity .32s ease }
.ob-leave-active { transition: opacity .16s ease }
.ob-enter-from, .ob-leave-to { opacity: 0 }
.ob-enter-active .modal {
  transition: transform .55s cubic-bezier(.22, 1, .36, 1), opacity .5s cubic-bezier(.22, 1, .36, 1);
  will-change: transform, opacity;
}
.ob-enter-from .modal { transform: translateY(26px) scale(.96); opacity: 0 }
.ob-leave-active .modal { transition: transform .16s ease, opacity .16s ease }
.ob-leave-to .modal { transform: translateY(10px) scale(.985); opacity: 0 }
.ob-enter-active .modal-head { animation: obFadeUp .5s cubic-bezier(.22, 1, .36, 1) .1s backwards }
.ob-enter-active .ob-tip { animation: obFadeUp .5s cubic-bezier(.22, 1, .36, 1) .16s backwards }
.ob-enter-active .form-item { animation: obFadeUp .5s cubic-bezier(.22, 1, .36, 1) backwards }
.ob-enter-active .form-item:nth-child(3) { animation-delay: .22s }
.ob-enter-active .form-item:nth-child(4) { animation-delay: .27s }
.ob-enter-active .form-item:nth-child(5) { animation-delay: .32s }
.ob-enter-active .form-item:nth-child(6) { animation-delay: .37s }
.ob-enter-active .form-item:nth-child(7) { animation-delay: .42s }
.ob-enter-active .ob-actions { animation: obFadeUp .5s cubic-bezier(.22, 1, .36, 1) .48s backwards }
</style>
