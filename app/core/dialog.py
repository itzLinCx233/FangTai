"""多轮对话状态机（评分项③：上下文一致性 / 最小化修改 / 交互自然度）。

设计要点：
- 结构化会话状态：已确认约束栈 + 当前方案（含锁定菜品），跨轮持久
- 意图与槽位经一次轻量 LLM 调用联合抽取（JSON），规则兜底
- 最小化修改：新增约束只替换"违规菜品"，未受影响的已确认菜品保留并在回复中说明
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from app.core.constraint_engine import ProfileConstraints, merge_constraints, TASTE_PREF_MAP
from app.core.recipe_store import Recipe

# ---------------------------------------------------------------- 意图定义
INTENTS = ("new_request", "add_constraint", "replace_dish", "reject_all",
           "question", "banquet", "smalltalk", "clarify")

_INTENT_RULES = [
    ("reject_all", re.compile(r"都不?(喜欢|行|要)|重(新)?(推荐|想|安排)|换一(套|桌)|全换")),
    ("replace_dish", re.compile(r"(换掉|不要|去掉|替换|换成).{0,6}$|把这个?换|不喜欢.{0,8}换")),
    ("banquet", re.compile(r"宴请|请客|聚餐|一桌|招待|来家里吃|几个人.{0,6}(吃|饭)|做东")),
    ("add_constraint", re.compile(r"别|不要|少(放|油|盐|糖)|不吃|忌|过敏|太(辣|甜|咸|油)|清淡|快点|时间|分钟|半小时|以内|多(久)|几个人|两个人|\d+人")),
    ("question", re.compile(r"[?？]|吗|什么|怎么|能不能|多久|多少")),
    ("new_request", re.compile(r"推荐|想吃|安排|想个|设计|配|做.{0,4}(饭|菜|餐|早餐|午饭|晚饭)|吃(啥|什么)|帮我想")),
]

MEAL_PAT = [
    ("早餐", re.compile(r"早餐|早饭|早点|早晨")),
    ("午餐", re.compile(r"午餐|午饭|中午")),
    ("晚餐", re.compile(r"晚餐|晚饭|晚上|今晚|夜宵|宵夜")),
    ("下午茶", re.compile(r"下午茶|点心|茶点")),
]

COUNT_CN = {"一": 1, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
            "八": 8, "九": 9, "十": 10}


@dataclass
class TurnSlots:
    """本轮抽取的槽位（增量约束）。"""
    intent: str = "new_request"
    meal: str | None = None
    people: int | None = None
    time_limit_min: int | None = None
    dish_count: int | None = None
    soup_needed: bool | None = None
    banned_add: list[str] = field(default_factory=list)   # 新增忌口（口味/食材）
    taste_add: list[str] = field(default_factory=list)    # 口味要求（正向）
    keywords: list[str] = field(default_factory=list)     # 主题关键词（面/鱼/清淡...）
    ambiguous: bool = False                               # 需主动澄清
    clarify_question: str = ""


# 口味正/反向词（"别做辣的"→banned 辣；"想吃辣"→taste 辣）
_TASTE_WORDS = ["辣", "甜", "咸", "酸", "清淡", "油", "蒜", "麻"]
_NEG_PAT = re.compile(r"别|不要|不(太|想|能|要|吃|放)|少(放|点|吃)|忌|拒|太")
_TIME_PAT = re.compile(r"(?:(\d+)|(半)|(一))?\s*(个)??(小时|分钟)")
_NUM_PAT = re.compile(r"(\d+)\s*(个人?|人|位)")
_CN_NUM_PAT = re.compile(r"([一二两三四五六七八九十])\s*(个人?|人|位)")
_DISH_COUNT_PAT = re.compile(r"([一二两三四五六七八九十]|\d+)\s*(个?菜|[菜道汤])")
_SOUP_PAT = re.compile(r"汤")


def rule_slots(message: str) -> TurnSlots:
    """规则优先抽取（零延迟），LLM 抽取兜底复杂情况。"""
    s = TurnSlots()
    msg = message.strip()
    for intent, pat in _INTENT_RULES:
        if pat.search(msg):
            s.intent = intent
            break
    for meal, pat in MEAL_PAT:
        if pat.search(msg):
            s.meal = meal
            break
    m = _NUM_PAT.search(msg) or _CN_NUM_PAT.search(msg)
    if m:
        val = m.group(1)
        s.people = COUNT_CN.get(val, None) or (int(val) if val and val.isdigit() else None)
    m2 = _TIME_PAT.search(msg)
    if m2:
        num = m2.group(1) or (0.5 if m2.group(2) else (1 if m2.group(3) else None))
        if num is not None:
            unit = m2.group(4)
            # "半小时"→group2 命中；"1小时"→小时
            if m2.group(2) or unit == "小时" or "小时" in msg[max(0, m2.start()-2):m2.end()+2]:
                s.time_limit_min = int(float(num) * 60)
            else:
                s.time_limit_min = int(float(num))
    m3 = _DISH_COUNT_PAT.search(msg)
    if m3:
        val = m3.group(1)
        s.dish_count = COUNT_CN.get(val) or (int(val) if val.isdigit() else None)
    if _SOUP_PAT.search(msg):
        s.soup_needed = True
        # "四菜一汤"语义 = 4菜 + 1汤 = 5 道
        if s.dish_count and re.search(rf"[菜].{{0,3}}汤", msg):
            s.dish_count += 1
    # 口味正/反向
    for w in _TASTE_WORDS:
        if w in msg:
            window = msg[max(0, msg.find(w) - 6):msg.find(w) + len(w)]
            if _NEG_PAT.search(window):
                s.banned_add.append(w)
            else:
                s.taste_add.append(w)
    # 别的明确忌口食材词
    for kw in re.findall(r"(?:不要|不吃|别放|避免)[的]?(.{1,4}?)(?:的|了|$|，|。|！)", msg):
        kw = kw.strip()
        if kw and len(kw) <= 4:
            s.banned_add.append(kw)
    # 主题关键词
    for kw in ["面", "米饭", "粥", "汤", "鱼", "虾", "鸡", "牛肉", "素食", "减脂餐", "便当", "烘焙"]:
        if kw in msg:
            s.keywords.append(kw)
    return s


NEW_REQUEST_PAT = re.compile(
    r"推荐|想吃|安排|想个|设计|配[个一]|做.{0,4}(饭|菜|餐|早餐|午饭|晚饭)|吃(啥|什么)|帮我想|今晚|中午吃|早上吃"
)


async def extract_slots(message: str, history_summary: str, llm,
                        fast_path: bool = False) -> TurnSlots:
    """LLM 联合抽取意图+槽位；规则兜底。失败时退回规则。

    fast_path=True 跳过 LLM（简单首轮请求规则已足够），零额外延迟。
    """
    base = rule_slots(message)
    if fast_path:
        if NEW_REQUEST_PAT.search(message):
            base.intent = "new_request"
        return base
    sys_prompt = """你是膳食推荐系统的语义解析器。把用户消息解析为 JSON（只输出 JSON，不要多余文字）：
{"intent":"new_request|add_constraint|replace_dish|reject_all|question|banquet|smalltalk|clarify",
 "meal":"早餐|午餐|晚餐|下午茶|null",
 "people":数字或null, "time_limit_min":数字或null,
 "dish_count":数字或null, "soup_needed":true/false/null,
 "banned_add":["新增忌口的口味或食材"],"taste_add":["想要的口味"],
 "keywords":["主题关键词如 面/鱼/减脂餐"],"ambiguous":true/false,
 "clarify_question":"ambiguous时给出一句澄清问话，否则空串"}
规则：
- intent 判定参考对话历史摘要：" + (history_summary or "无") + "
- "替换某道菜"=replace_dish；"整个方案都不要"=reject_all；"多个人聚餐/宴请"=banquet
- 在上一轮方案上追加限制（含多人口味冲突/矛盾需求）=add_constraint；全新需求=new_request
- 用户需求模糊（如"有仪式感""清爽"但无具体方向）时 ambiguous=true 并构造澄清问题
- banned_add 只放用户明确不想要的（"别辣"→["辣"]），不要臆测"""
    try:
        import json as _json
        raw = await llm.chat_fast([
            llm.system(sys_prompt), llm.user(message),
        ], temperature=0.1)
        raw = raw.strip()
        start, end = raw.find("{"), raw.rfind("}")

        def clean(v):
            return None if v in ("null", "无", "", "none", "None") else v

        if start >= 0 and end > start:
            data = _json.loads(raw[start:end + 1])
            s = TurnSlots(
                intent=clean(data.get("intent")) or base.intent,
                meal=clean(data.get("meal")) or base.meal,
                people=clean(data.get("people")) or base.people,
                time_limit_min=clean(data.get("time_limit_min")) or base.time_limit_min,
                dish_count=clean(data.get("dish_count")) or base.dish_count,
                soup_needed=data.get("soup_needed") if data.get("soup_needed") is not None else base.soup_needed,
                banned_add=list(set(base.banned_add + (data.get("banned_add") or []))),
                taste_add=list(set(base.taste_add + (data.get("taste_add") or []))),
                keywords=list(set(base.keywords + (data.get("keywords") or []))),
                ambiguous=bool(data.get("ambiguous")),
                clarify_question=data.get("clarify_question") or "",
            )
            return s
    except Exception as e:
        print(f"[dialog] 槽位抽取退化为规则模式: {e}")
    return base


# ---------------------------------------------------------------- 会话状态
@dataclass
class DishPlanItem:
    recipe_id: int
    name: str
    role: str = "菜"          # 主食/主菜/素菜/汤/小菜
    reason: str = ""
    locked: bool = False      # 用户已确认，最小化修改时保留


@dataclass
class DialogSession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    profile_ids: list[int] = field(default_factory=list)
    constraints: ProfileConstraints = field(default_factory=ProfileConstraints)
    history: list[dict] = field(default_factory=list)      # {role, content}
    current_plan: list[DishPlanItem] = field(default_factory=list)
    meal: str | None = None
    people: int = 2
    time_limit_min: int | None = None
    dish_count: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def set_profiles(self, profile_ids: list[int], profile_lookup):
        self.profile_ids = list(profile_ids)
        cons = [ProfileConstraints.from_profile(profile_lookup(pid)) for pid in self.profile_ids]
        self.constraints = merge_constraints(cons) if cons else ProfileConstraints({})

    def add_history(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        self.updated_at = time.time()
        if len(self.history) > 24:
            self.history = self.history[-24:]

    def history_summary(self, last_n: int = 6) -> str:
        parts = []
        for h in self.history[-last_n:]:
            prefix = "用户" if h["role"] == "user" else "助手"
            parts.append(f"{prefix}：{h['content'][:120]}")
        plan = self.plan_summary()
        return "\n".join(parts) + (f"\n当前方案：{plan}" if plan else "")

    def plan_summary(self) -> str:
        if not self.current_plan:
            return ""
        return "、".join(f"{d.name}[{d.recipe_id}]{('(已确认)' if d.locked else '')}" for d in self.current_plan)

    def apply_slots(self, slots: TurnSlots):
        """增量应用槽位到会话状态（上下文一致性：不遗忘旧约束）。

        口味冲突以最新对话为准：先前会话追加的同口味忌口被本轮正向口味解除
        （"别做辣的" → "还是来点辣的"）。过敏/疾病忌口来自档案，不在此解除。
        同一轮既禁又要的口味（多人口味分歧）保守保留忌口，交由 LLM 选菜权衡。
        """
        if slots.meal:
            self.meal = slots.meal
        if slots.people:
            self.people = slots.people
        if slots.time_limit_min:
            self.time_limit_min = slots.time_limit_min
        if slots.dish_count:
            self.dish_count = slots.dish_count
        if slots.banned_add:
            self.constraints.add_banned([b for b in slots.banned_add if b])
        if slots.taste_add:
            conflicted = set(slots.banned_add)
            for t in slots.taste_add:
                canon = TASTE_PREF_MAP.get(t, t)
                if not canon:
                    continue
                if canon not in conflicted:
                    self.constraints.remove_banned(canon)
                if canon not in (self.constraints.taste_pref or ""):
                    self.constraints.taste_pref = canon
                    break

    def label_filter(self) -> dict[str, list[str]]:
        """餐次作为硬过滤（早餐/午餐/晚餐/下午茶标签覆盖充分）；口味仅作软加权。"""
        filt: dict[str, list[str]] = {}
        if self.meal:
            filt["餐次"] = [self.meal]
        return filt

    def violating_dishes(self) -> list[DishPlanItem]:
        """当前方案中违反最新约束的菜品（过敏/忌口/时间限制），将被替换；其余保留。"""
        from app.core.recipe_store import get_store, estimate_minutes
        out = []
        for d in self.current_plan:
            rec = get_store().get(d.recipe_id)
            if rec is None or not self.constraints.ok(rec):
                out.append(d)
            elif self.time_limit_min and estimate_minutes(rec) > self.time_limit_min:
                out.append(d)
        return out
