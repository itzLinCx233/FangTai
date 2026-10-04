<template>
  <div class="chat-page">
    <!-- 顶栏：用户中心（原健康档案下拉位置） -->
    <header>
      <div class="logo">🍲</div>
      <div>
        <h1>个性化膳食规划 Agent</h1>
        <div class="sub">方太 AI 专项赛 · RAG + 多约束推理 + 营养估算</div>
      </div>
      <div class="spacer"></div>
      <div class="controls">
        <button class="btn" @click="newChat">新对话</button>
        <div class="user-box">
            <div class="avatar" :class="{ admin: auth.isAdmin }" @click="menuOpen = !menuOpen">
              {{ avatarChar }}
            </div>
            <div class="user-name" @click="menuOpen = !menuOpen">{{ auth.user!.nickname || auth.user!.username }}</div>
            <div class="user-menu" v-show="menuOpen">
              <a @click="openCenter">个人中心</a>
              <a v-if="auth.isAdmin" @click="goAdmin">管理后台</a>
              <div class="sep"></div>
              <a class="logout" @click="doLogout">退出登录</a>
            </div>
          </div>
      </div>
    </header>

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
          <h4>📋 本餐方案（{{ it.plan.people || 2 }}人 · {{ it.plan.meal || '' }}）</h4>
          <div class="dishes">
            <div class="dish" v-for="d in it.plan.dishes" :key="d.id">
              <div class="name">{{ d.name }}
                <span class="role" :class="{ soup: d.role === '汤' }">{{ d.role }}</span>
                <span v-if="d.kept" class="kept">沿用</span>
              </div>
              <div class="meta">{{ (d.ingredients || []).slice(0, 6).join('、') }}</div>
              <div class="reason" v-if="d.reason">💡 {{ d.reason }}</div>
            </div>
          </div>
          <div class="nutri">
            <span>每人约 <b>{{ it.plan.nutrition_per_person?.intake?.kcal ?? '-' }}</b> kcal</span>
            <span>蛋白质 <b>{{ it.plan.nutrition_per_person?.intake?.protein_g ?? '-' }}</b>g</span>
            <span>脂肪 <b>{{ it.plan.nutrition_per_person?.intake?.fat_g ?? '-' }}</b>g</span>
            <span>碳水 <b>{{ it.plan.nutrition_per_person?.intake?.carbs_g ?? '-' }}</b>g</span>
            <span class="tag">{{ it.plan.nutrition_per_person?.purine_level }}</span>
          </div>
          <div class="constraints">✅ 已满足约束：{{ it.plan.constraints_applied || '' }}</div>
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

    <!-- 个人中心模态框 -->
    <div class="modal-mask" v-if="ucMask" @click.self="ucMask = false">
      <div class="modal wide">
        <div class="modal-head"><h3>个人中心</h3><button class="close-x" @click="ucMask = false">×</button></div>
        <div class="modal-body" v-if="auth.user">
          <div class="uc-section"><h4>📊 账号信息</h4>
            <div class="uc-info">用户名：<b>{{ auth.user.username }}</b> ｜ 角色：{{ auth.user.role === 'admin' ? '管理员' : '普通用户' }}<br>
              注册时间：{{ auth.user.created_at }} ｜ 最后登录：{{ auth.user.last_login_at || '-' }}</div>
          </div>
          <div class="uc-section"><h4>👤 资料设置</h4>
            <div class="form-item"><label>昵称</label><input v-model="uc.nick"></div>
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
  busy.value = true
  input.value = ''
  items.value.push({ type: 'msg', role: 'user', text, done: true })
  const ai = { type: 'msg' as const, role: 'ai' as const, text: '', done: false }
  items.value.push(ai)
  const t0 = performance.now()
  let firstTok: number | null = null
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
        if (ev.type === 'plan') items.value.push({ type: 'plan', plan: ev })
        else if (ev.type === 'delta') {
          if (firstTok === null) firstTok = performance.now() - t0
          ai.text += ev.text
        } else if (ev.type === 'clarify') {
          // 澄清问题随后会以 delta 流式下发，这里不重复拼接
        } else if (ev.type === 'done') {
          statsLine.value = `首Token ${firstTok ? (firstTok / 1000).toFixed(2) : '-'}s · 端到端 ${((performance.now() - t0) / 1000).toFixed(2)}s`
        }
        scrollBottom()
      }
    }
    ai.done = true
  } catch (e: any) {
    ai.text = '出错了：' + e.message
    ai.done = true
  } finally { busy.value = false; scrollBottom() }
}

function newChat() { sessionId.value = null; items.value = []; statsLine.value = '' }

function doLogout() {
  menuOpen.value = false
  auth.logout()
  router.push('/login')
}
function goAdmin() { menuOpen.value = false; router.push('/admin') }

/* ---------- 个人中心 ---------- */
const ucMask = ref(false)
const uc = ref({ nick: '', companions: [] as number[] })
const pw = ref({ old: '', new: '' })
const ucSessions = ref<any[]>([])
const sessionView = ref<any[]>([])

async function openCenter() {
  menuOpen.value = false
  const u = auth.user!
  uc.value.nick = u.nickname || ''
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
    body: JSON.stringify({ nickname: uc.value.nick }),
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

onMounted(async () => {
  auth.refresh()
  try {
    const d = await fetch('/api/profiles?kind=simple').then(r => r.json())
    profiles.value = d.items
  } catch { /* ignore */ }
})
</script>

<style scoped>
.chat-page { height: 100%; display: flex; flex-direction: column }
header { background: var(--card); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 14px; }
header .logo { width: 38px; height: 38px; border-radius: 10px; background: linear-gradient(135deg, var(--primary), #1a9b7e); display: flex; align-items: center; justify-content: center; font-size: 20px }
header h1 { font-size: 17px; font-weight: 600 }
header .sub { font-size: 12px; color: var(--muted) }
.spacer { flex: 1 }
.controls { display: flex; gap: 10px; align-items: center }
.user-box { position: relative; display: flex; align-items: center; gap: 8px }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: linear-gradient(135deg, #5470c6, #7a90da); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; cursor: pointer; user-select: none }
.avatar.admin { background: linear-gradient(135deg, var(--accent), #f0b95f) }
.user-name { font-size: 13px; font-weight: 500; cursor: pointer }
.user-menu { position: absolute; right: 0; top: 44px; background: #fff; border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.1); min-width: 150px; padding: 6px; z-index: 30 }
.user-menu a { display: block; padding: 9px 12px; border-radius: 7px; font-size: 13px; cursor: pointer }
.user-menu a:hover { background: var(--primary-light); color: var(--primary) }
.user-menu .logout { color: var(--danger) }
.user-menu .sep { height: 1px; background: var(--border); margin: 4px 6px }
main { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 18px }
.msg { display: flex; gap: 12px; max-width: 860px; width: 100%; margin: 0 auto }
.msg .avatar-c { width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 15px; color: #fff; background: var(--primary) }
.msg.user .avatar-c { background: #5470c6 }
.bubble { padding: 12px 16px; border-radius: 12px; line-height: 1.7; font-size: 14.5px; white-space: pre-wrap; word-break: break-word; max-width: calc(100% - 60px); background: var(--card); border: 1px solid var(--border) }
.msg.user .bubble { background: var(--primary); color: #fff; border: none }
.bubble.system { background: #f0f7f5; border-color: #cfe5df; color: #3c6b5d; font-size: 13px }
.bubble.typing::after { content: '●●●'; color: var(--muted); animation: blink 1.2s infinite; letter-spacing: 2px; font-size: 10px }
@keyframes blink { 0%, 80%, 100% { opacity: .25 } 40% { opacity: 1 } }
.plan-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; max-width: 860px; width: 100%; margin: 0 auto }
.plan-card h4 { font-size: 13px; color: var(--primary); margin-bottom: 10px }
.dishes { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px }
.dish { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: #fbfcfd }
.dish .name { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap }
.dish .role { font-size: 11px; background: var(--primary-light); color: var(--primary); padding: 1px 8px; border-radius: 10px }
.dish .role.soup { background: #fdf1dd; color: #a06a10 }
.dish .kept { font-size: 11px; background: #eef4ff; color: #3f6ac6; padding: 1px 8px; border-radius: 10px }
.dish .meta { font-size: 12px; color: var(--muted); margin-top: 6px; line-height: 1.5 }
.dish .reason { font-size: 12px; color: #4a5361; margin-top: 4px }
.nutri { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border); display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: #4a5361 }
.nutri b { color: var(--text) }
.nutri .tag { background: var(--primary-light); color: var(--primary); border-radius: 8px; padding: 2px 10px }
.constraints { margin-top: 8px; font-size: 12px; color: var(--muted) }
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
.form-item .hint { font-size: 12px; color: var(--muted); margin-top: 4px }
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
</style>
