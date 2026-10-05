"""Agent 编排：意图/槽位抽取 → 约束过滤 → 混合检索 → LLM 选菜(零幻觉) →
组合校验(零违反) → 流式生成说明。

SSE 事件流（供 Web/评测消费）：
  intent  → 槽位解析结果
  plan    → 结构化方案（菜品/角色/营养/多样性，机器可校验）
  delta   → 正文增量 token
  done    → 计时统计
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

from app import config
from app.core import dialog as dlg
from app.core import planner
from app.core.constraint_engine import HEALTH_NEED_KEYWORDS, ProfileConstraints
from app.core.llm import get_llm
from app.core.nutrition import (
    Nutrition, recipe_nutrition, balance_report, adaptive_servings,
)
from app.core.profiles import get_profile, profile_text
from app.core.recipe_store import Recipe, get_store, estimate_minutes
from app.core.retriever import get_retriever


# ---------------------------------------------------------------- 候选构造
# 餐次冲突映射：目标餐次需排除的餐次标签（无餐次标签的菜视为通用，不排除）
MEAL_CONFLICTS: dict[str, set[str]] = {
    "早餐": {"午餐", "晚餐"},
    "午餐": {"早餐"},
    "晚餐": {"早餐"},
}


def meal_conflict_ok(rec: Recipe, meal: str | None) -> bool:
    """菜谱与目标餐次是否兼容：命中目标餐次标签，或不含任何冲突餐次标签。"""
    if not meal:
        return True
    tags = set(rec.tags.get("餐次", []))
    return meal in tags or not (tags & MEAL_CONFLICTS.get(meal, set()))


def candidate_pool(session: dlg.DialogSession, query: str, need: int = 24,
                   use_label_filter: bool = True) -> list[Recipe]:
    """约束过滤 + 混合检索 → 候选菜谱池（口味为软加权，餐次为硬过滤可放宽）。

    设备限制（无烤箱/炸锅）与跨轮去重（历轮已推荐过的菜不再入选，
    reject_all 换桌同样不吃回头菜）在 ok_ids 阶段排除；历史菜品排除后
    候选枯竭时放宽（约束安全性优先于去重体验）。
    """
    store = get_store()
    ok_ids = store.filter_ids(session.constraints.banned_pattern)
    if session.banned_methods:
        ok_ids = {rid for rid in ok_ids
                  if not dlg.method_violations(store.by_id[rid], session.banned_methods)}
    if session.served_ids:
        fresh = ok_ids - session.served_ids
        if len(fresh) >= max(8, need):
            ok_ids = fresh
    label_filter = session.label_filter() if use_label_filter else {}
    if label_filter:
        # 标签过滤后候选不足时逐级放宽（先放宽餐次；冲突餐次在检索后仍会硬排除）
        hits = {rid for rid in ok_ids if all(
            any(w in store.by_id[rid].tags.get(d, []) for w in v)
            for d, v in label_filter.items())}
        if len(hits) < max(8, need // 2):
            label_filter = {}
    boost: list[str] = []
    for need_name in session.constraints.health_needs:
        boost += HEALTH_NEED_KEYWORDS.get(need_name, [])
    if session.constraints.taste_pref:
        boost.extend(t for t in session.constraints.taste_pref.split("、") if t)
    results = get_retriever().search(
        query, ok_ids=ok_ids, label_filter=label_filter,
        keyword_boost=boost[:12], topk=need,
    )
    pool = [r.recipe for r in results]
    # 餐次冲突硬排除（如晚餐剔除早餐菜；无餐次标签视为通用），严重不足时回退原池
    if session.meal:
        strict = [r for r in pool if meal_conflict_ok(r, session.meal)]
        if len(strict) >= max(4, need // 2):
            pool = strict
    # 时间限制过滤
    if session.time_limit_min:
        pool = [r for r in pool if estimate_minutes(r) <= session.time_limit_min] or pool
    return pool


def filter_by_meal(pool: list[Recipe], meal: str | None, need: int) -> list[Recipe]:
    """并行粗检索后的餐次后过滤：剔除与目标餐次明确冲突的菜（如晚餐剔除早餐菜），
    无餐次标签视为通用保留；剔除后严重不足时回退原池。"""
    if not meal:
        return pool
    filt = [r for r in pool if meal_conflict_ok(r, meal)]
    return filt if len(filt) >= max(4, need // 2) else pool


# ---------------------------------------------------------------- 选菜（零幻觉）
SELECT_SYS = """你是膳食选菜引擎。只允许从候选列表中选菜，绝不使用列表外的菜。
输出严格 JSON：{"dishes":[{"id":候选id,"role":"主菜|荤菜|素菜|蛋类|豆制品|汤|主食|小菜","reason":"20字左右的推荐理由，点出与用户口味/健康需求/忌口的关联"}]}要求：
1. 严格满足每个人的忌口/过敏（候选已过滤，仍需复核）
2. 荤素搭配合理，优先照顾用户健康需求与口味；理由需说清该菜照顾了谁（如"低脂适合减脂""软烂适合老人"）
3. 菜数等于要求数量，不许多选
4. 每道菜都必须写 reason 字段：20字左右的推荐理由，任何一道不得留空、不得省略
5. 除用户明确要求多汤外，全桌至多 1 道汤，其余选炒/蒸/烤/拌等菜品
6. 兼顾整桌营养均衡：避免多道高热量菜（油炸/五花肉/芝士类）叠加，素菜不过半也不可或缺
只输出 JSON。"""

REASON_SYS = """你是膳食推荐理由生成器。根据菜品信息与用户需求，写一句20字左右的中文推荐理由，说明为什么推荐这道菜。
只输出理由本身：不要以菜名开头，不要引号，不要句号结尾。"""


async def llm_select(
    session: dlg.DialogSession, msg: str, pool: list[Recipe], dish_count: int,
    soup_needed: bool | None, avoid_ids: set[int] | None = None,
    avoid_hard: bool = False,
) -> list[tuple[Recipe, str]]:
    """LLM 从候选池选菜；返回 [(recipe, reason)]。失败/违规时回退规则组合。

    avoid_ids（重复度治理）：上一轮已推荐菜品。软回避=在池中沉底（LLM 优先选其他），
    硬回避=直接剔除（用户明确要求不重复）；剔空则放弃回避保兜底。
    """
    llm = get_llm()
    pool = list(pool)
    avoid_names = ""
    if avoid_ids:
        by_id = {r.id: r for r in pool}
        avoid_names = "、".join(by_id[i].name for i in avoid_ids if i in by_id)[:150]
        if avoid_hard:
            kept = [r for r in pool if r.id not in avoid_ids]
            pool = kept or pool
        else:
            pool.sort(key=lambda r: r.id in avoid_ids)  # 稳定排序：回避菜沉底
    lines = [r.brief() for r in pool[: config.FINAL_CANDIDATES + 8]]
    task_lines = [f"任务：为{session.meal or '一餐'}（{session.people}人）选 {dish_count} 道菜"]
    if soup_needed:
        task_lines.append("其中应包含 1 道汤")
    tastes = [t for t in (session.constraints.taste_pref or "").split("、") if t]
    if len(tastes) > 1:
        task_lines.append(
            f"口味兼顾：用餐人中存在 {'与'.join(tastes)} 的不同偏好，"
            "两类口味的菜都要安排，理由里点明各照顾谁")
    if avoid_names:
        mode_txt = "已从候选剔除，不得推荐" if avoid_hard else "排在候选末尾，除非用户本轮点名否则勿选"
        task_lines.append(f"重复度控制：{avoid_names} 此前已推荐过（{mode_txt}）")
    banned_ctx = (session.constraints.allergens + session.constraints.taboo_keys
                  + session.constraints.extra_banned)
    if banned_ctx:
        task_lines.append("用户忌口/过敏（菜与理由描述都不得违背，如忌辣则理由不得写辣/酸辣）："
                          + "、".join(banned_ctx))
    profile_lines = []
    for pid in session.profile_ids:
        p = get_profile(pid)
        if p:
            profile_lines.append(profile_text(pid))
    prompt = "\n".join(task_lines) + (
        "\n用餐人档案：\n" + "\n".join(profile_lines) if profile_lines else ""
    ) + f"\n本轮需求：{msg}\n候选列表：\n" + "\n".join(lines)
    try:
        raw = await llm.chat_fast([llm.system(SELECT_SYS), llm.user(prompt)], temperature=0.6)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        picked: list[tuple[Recipe, str]] = []
        seen: set[int] = set()
        pool_by_id = {r.id: r for r in pool}
        for d in data.get("dishes", []):
            rid = int(d.get("id", -1))
            if rid in pool_by_id and rid not in seen and session.constraints.ok(pool_by_id[rid]):
                # 理由可能为空（LLM 偶发漏写）：透传空，前端先出卡再调 /api/dish_reason 重试生成
                picked.append((pool_by_id[rid], str(d.get("reason", "")).strip()))
                seen.add(rid)
        if picked:
            return picked[: dish_count + 2]
    except Exception as e:
        print(f"[agent] LLM 选菜异常，回退规则组合: {e}")
    # 规则回退
    combo = planner.build_combo(
        pool, people=session.people, dish_count=dish_count,
        soup_needed=soup_needed, meal=session.meal,
    )
    return [(d, "荤素冷热搭配均衡，覆盖本餐营养需求") for d in combo.dishes[: dish_count + 2]]


def finalize_plan(
    session: dlg.DialogSession, picked: list[tuple[Recipe, str]], dish_count: int,
    soup_needed: bool | None, pool: list[Recipe] | None = None,
) -> list[dlg.DishPlanItem]:
    """LLM 选择优先；组合规划器补齐；汤数/多样性校验；末次零违反扫描。"""
    combo = planner.build_combo(
        [r for r, _ in picked], people=session.people, dish_count=dish_count,
        soup_needed=soup_needed, meal=session.meal,
    )
    by_role = dict(combo.roles)
    reason_of = {r.id: why for r, why in picked}
    for r, _ in picked:
        if r.id not in by_role:
            by_role[r.id] = "荤菜" if r.category["meat_type"] == "荤" else "素菜"
    chosen_ids = [r.id for r, _ in picked]
    extra = [r for r in combo.dishes if r.id not in chosen_ids]
    ordered = [r for r, _ in picked][: dish_count] + extra[: max(0, dish_count - len(picked))]

    # 汤数控制：至多1道汤（用户未要求多汤时），多余汤用池中非汤菜品替换
    if dish_count > 1 and ordered:
        soup_cnt = sum(1 for r in ordered if planner.is_soup(r))
        if soup_cnt > 1:
            keep_soup = next(r for r in ordered if planner.is_soup(r))
            replacements: list[Recipe] = []
            for r in (pool or []):
                if len(replacements) >= soup_cnt - 1:
                    break
                if (r.id not in {x.id for x in ordered} and not planner.is_soup(r)
                        and session.constraints.ok(r)):
                    replacements.append(r)
            new_ordered, used = [], set()
            for r in ordered:
                if planner.is_soup(r) and r.id != keep_soup.id and replacements:
                    rep = replacements.pop(0)
                    new_ordered.append(rep)
                    used.add(rep.id)
                else:
                    new_ordered.append(r)
            ordered = new_ordered

    final: list[dlg.DishPlanItem] = []
    for r in ordered:
        # 末次零违反扫描（前置过滤下通常不触发）
        if not session.constraints.ok(r):
            continue
        final.append(dlg.DishPlanItem(
            recipe_id=r.id, name=r.name,
            role=by_role.get(r.id, "菜"), reason=reason_of.get(r.id, ""),
        ))
    return final


# ---------------------------------------------------------------- 方案营养
def plan_nutrition(session: dlg.DialogSession) -> tuple[Nutrition, dict]:
    store = get_store()
    total = Nutrition()
    for d in session.current_plan:
        rec = store.get(d.recipe_id)
        if rec:
            total.add(recipe_nutrition(rec, servings=adaptive_servings(rec, session.people)))
    labor = "中"
    for pid in session.profile_ids:
        p = get_profile(pid)
        if p and p.get("劳动强度"):
            labor = p["劳动强度"]
            break
    return total, balance_report(total, labor)


# ---------------------------------------------------------------- 主流程
class MealAgent:
    def __init__(self):
        self.llm = get_llm()

    async def stream_chat(
        self, session: dlg.DialogSession, message: str
    ) -> AsyncIterator[dict]:
        t0 = time.perf_counter()
        first_ts: list[float] = []
        store = get_store()

        def emit(ev: dict) -> dict:
            if not first_ts and ev.get("type") in ("delta", "clarify", "intent"):
                first_ts.append(time.perf_counter() - t0)
            return ev

        # 1) 槽位抽取 与 粗检索 并行（明确命中请求模式的简单首轮走规则快路径；
        #    未命中任何模式的消息必须走 LLM 抽取，保证模糊需求能被识别并主动澄清）
        is_first_simple = (
            not session.current_plan
            and not session.history
            and bool(dlg.NEW_REQUEST_PAT.search(message))
            and dlg.rule_slots(message).intent in ("new_request", "banquet")
        )
        slots_task = asyncio.create_task(
            dlg.extract_slots(message, session.history_summary(), self.llm,
                              fast_path=is_first_simple))
        pool_task = asyncio.to_thread(
            candidate_pool, session, message, 40, False)
        slots = await slots_task
        session.apply_slots(slots)
        kept_ids: set[int] = set()
        replace_targets: list[dlg.DishPlanItem] = []

        # 3.05) 换桌短语规则路由：意图分类器对"换一桌/全部换掉"易误判为 add_constraint，
        #       走"零违规不变"分支导致跨轮去重失效 → 强制 reject_all 全新选菜
        if session.current_plan and re.search(
                r"换一(整)?桌|全部换掉|整桌换|重新来一(桌|份)|重换一", message):
            slots.intent = "reject_all"

        # 3.1) 加菜意图：在现有方案上追加（"再来点辣菜/加一道汤/想喝汤"），
        #      已有菜品全部锁定保留，只选新增的 1-2 道
        if (session.current_plan
                and re.search(r"再来[一2两1]?[点道个份]|再添|再加[一1两2]?[道个份]|"
                              r"加[一1两2]?[道个份](?!油|盐|糖|水)|添[一1两2]?[道个份]|多[加来][一1两2]?[道个份]|"
                              r"(想|要)喝[点一]?汤|来[一1]?[碗份]汤", message)):
            slots.intent = "add_dish"
            # 加菜轮同时提了新约束（如"家里没烤箱"）→ 不合规旧菜同样替换
            replace_targets = session.violating_dishes()
            for d in session.current_plan:
                d.locked = d.recipe_id not in {x.recipe_id for x in replace_targets}
            kept_ids = {d.recipe_id for d in session.current_plan if d.locked}
            # 加菜 = 在现有菜数上追加：更新总菜数，防止下游"超量裁剪"把新菜切掉
            add_n = 2 if re.search(r"[两2二][道个份]", message) else 1
            session.dish_count = len(session.current_plan) + add_n

        yield emit({"type": "intent", "slots": {
            "intent": slots.intent, "meal": session.meal,
            "people": session.people, "time_limit_min": session.time_limit_min,
        }})

        # 2) 矛盾需求 / 模糊需求 → 主动澄清（评分③交互自然度）
        has_profile_constraints = bool(
            session.profile_ids and (
                session.constraints.allergens or session.constraints.taboo_keys
                or session.constraints.health_needs or session.constraints.taste_pref)
        ) or bool(session.constraints.extra_banned)  # 用户资料忌口/会话忌口同样构成约束方向
        # 矛盾澄清仅拦单人自相矛盾；多人同桌的口味分歧（"小孩要辣老人忌辣"）
        # 不算矛盾，交由口味多值 + LLM 整桌分配权衡
        conflict_txt = (slots.conflict or dlg.detect_conflicts(slots)) \
            if len(session.profile_ids) <= 1 else ""
        if conflict_txt:
            session.add_history("user", message)
            q = (f"您提的需求里有点矛盾：{conflict_txt}。"
                 "想和您确认下以哪个为准？也可以折中处理（比如微辣、少放辣）～")
            async for ev in self._clarify_stream(session, q, emit, t0, first_ts, store):
                yield ev
            return
        if slots.ambiguous and not has_profile_constraints and not session.current_plan:
            session.add_history("user", message)
            q = slots.clarify_question or "方便说说您的偏好吗？比如想吃辣的还是清淡的、几个人吃？"
            async for ev in self._clarify_stream(session, q, emit, t0, first_ts, store):
                yield ev
            return

        # 3) 分支处理 → 统一得到 (plan_items, kept_ids, context_for_llm)
        #    （kept_ids/replace_targets 已在 3.05 前初始化、3.1 加菜分支中赋值）

        if slots.intent in ("smalltalk",) and not session.current_plan and "吃" not in message:
            reply = "您好！我是方太健康膳食助手，告诉我您想吃什么、几个人吃，我来为您搭配一餐～"
            session.add_history("user", message)
            session.add_history("assistant", reply)
            for tok in [reply[i:i + 6] for i in range(0, len(reply), 6)]:
                yield emit({"type": "delta", "text": tok})
            yield self._done(t0, first_ts, store)
            return

        # 3.05) 换桌短语规则路由：意图分类器对"换一桌/全部换掉"易误判为 add_constraint，
        #       走"零违规不变"分支导致跨轮去重失效 → 强制 reject_all 全新选菜
        if session.current_plan and re.search(
                r"换一(整)?桌|全部换掉|整桌换|重新来一(桌|份)|重换一", message):
            slots.intent = "reject_all"

        # 3.1) 加菜意图：在现有方案上追加（"再来点辣菜/加一道汤"），
        #      已有菜品全部锁定保留，只选新增的 1-2 道
        if (session.current_plan
                and re.search(r"再来[一2两1]?[点道个份]|再添|再加[一1两2]?[道个份]|"
                              r"加[一1两2]?[道个份](?!油|盐|糖|水)|添[一1两2]?[道个份]|多[加来][一1两2]?[道个份]", message)):
            slots.intent = "add_dish"
            # 加菜轮同时提了新约束（如"家里没烤箱"）→ 不合规旧菜同样替换
            replace_targets = session.violating_dishes()
            for d in session.current_plan:
                d.locked = d.recipe_id not in {x.recipe_id for x in replace_targets}
            kept_ids = {d.recipe_id for d in session.current_plan if d.locked}

        # 3.2) 重复度治理：全新推荐轮回避上一轮菜品（须在 reject_all 清空方案前取）
        avoid_ids: set[int] = set()
        avoid_hard = bool(re.search(
            r"换一?(批|波|组|套|桌|些)|换换口味?|来点不一样的?|重(新)?(推荐|来|安排)|"
            r"不要(和|跟)?(上次|之前|刚才|原来)(一样|重复)|别(再|老)重复", message))
        if slots.intent in ("new_request", "banquet", "reject_all") and session.current_plan:
            # 用户本轮点名提到的菜不回避（如"还是想吃红烧排骨，重新配一套"）
            avoid_ids = {d.recipe_id for d in session.current_plan
                         if d.name[:2] not in message}
        if slots.intent in ("add_constraint", "replace_dish") and session.current_plan:
            # 最小化修改：只替换违规/被点名/与本轮新需求冲突的菜品，其余锁定
            replace_targets = session.violating_dishes()
            # 需求冲突时本轮优先：本轮明确提出新口味/新餐次，与现有菜品标签不符的替换
            new_tastes = {dlg.TASTE_PREF_MAP.get(t, t) for t in (slots.taste_add or []) if t}
            for d in session.current_plan:
                if d in replace_targets:
                    continue
                rec = store.get(d.recipe_id)
                if rec is None:
                    continue
                if new_tastes:
                    dish_tastes = set(rec.tags.get("口味", []))
                    if dish_tastes and not (dish_tastes & new_tastes):
                        replace_targets.append(d)
                        continue
                if slots.meal:
                    dish_meals = rec.tags.get("餐次", [])
                    if dish_meals and slots.meal not in dish_meals:
                        replace_targets.append(d)
            if slots.intent == "replace_dish":
                for d in session.current_plan:
                    if d.name[:2] in message or any(
                        k in message for k in ("主菜", "素菜", "汤") if d.role in k
                    ):
                        if d not in replace_targets:
                            replace_targets.append(d)
                # 用户点名换掉的默认是第一道未锁定菜
                if not replace_targets:
                    replace_targets = [d for d in session.current_plan if not d.locked][:1]
            kept_ids = {d.recipe_id for d in session.current_plan} - {d.recipe_id for d in replace_targets}
            for d in session.current_plan:
                d.locked = d.recipe_id in kept_ids
        elif slots.intent == "reject_all":
            session.current_plan = []

        need_count = session.dish_count or planner.default_dish_count(
            session.people, meal=session.meal)
        if slots.intent in ("add_constraint", "replace_dish") and session.current_plan:
            need_count = len(replace_targets)
        elif slots.intent == "add_dish":
            need_count = 2 if re.search(r"[两2二][道个份]", message) else 1
            if session.dish_count and len(session.current_plan) < session.dish_count:
                need_count = min(session.dish_count - len(session.current_plan), need_count) or 1

        # 3.5) 约束追加但当前方案零违规：方案保持不变，直接确认（最小化修改）
        # 菜数/人数缩减：先按最新要求裁剪现有方案（已确认菜品优先保留），再走确认流程
        want_now = session.dish_count or planner.default_dish_count(
            session.people, meal=session.meal)
        oversized = len(session.current_plan) > want_now
        if oversized and session.current_plan:
            session.current_plan.sort(key=lambda d: not d.locked)
            session.current_plan = session.current_plan[:want_now]
        if slots.intent == "add_constraint" and session.current_plan and not replace_targets:
            for d in session.current_plan:
                d.locked = True
            session.add_history("user", message)
            nutri, balance = plan_nutrition(session)
            plan_payload = self._plan_payload(session, balance)
            yield emit({"type": "plan", **plan_payload})
            preamble = self._preamble(session, slots, no_change=not oversized)
            for tok in [preamble[i:i + 8] for i in range(0, len(preamble), 8)]:
                yield emit({"type": "delta", "text": tok})
                await asyncio.sleep(0.005)
            hint = ("（提示：已按您的要求精简为 %d 道菜，保留原方案中您已确认的菜品）" % len(session.current_plan)
                    if oversized else "（提示：当前方案经校验已全部满足该新约束，无需调整）")
            expl = self._explanation_prompt(
                session, message + hint,
                balance, {d.recipe_id for d in session.current_plan}, [])
            got_text = False
            try:
                async for tok in self.llm.chat_stream(expl):
                    got_text = True
                    yield emit({"type": "delta", "text": tok})
            except Exception as e:
                print(f"[agent] 说明生成异常: {e}")
            if not got_text:
                yield emit({"type": "delta", "text": self._template_reply(session, [])})
            session.add_history("assistant", "（确认当前方案满足新约束）")
            yield self._done(t0, first_ts, store, plan=plan_payload)
            return

        # 前导确认句提前到选菜前发射：内容只依赖槽位/约束/替换目标，
        # 使首个 delta（TTFT 计时点）在意图抽取后即到达；选菜与说明在流中推进
        preamble = self._preamble(session, slots, replaced=replace_targets)
        for tok in [preamble[i:i + 8] for i in range(0, len(preamble), 8)]:
            yield emit({"type": "delta", "text": tok})
            await asyncio.sleep(0.005)

        # 4) 选菜池检索查询：只取本轮增量（用户消息本身）。
        #    既有上下文全部经结构化通道生效（ok_ids 约束白名单 / label_filter 餐次 /
        #    keyword_boost 口味与健康需求），不拼接上轮原文——避免历史措辞
        #    （如已被推翻的"来点辣的"）与本轮新需求同时进入检索污染召回。
        query = message
        session.add_history("user", message)
        if slots.intent in ("add_constraint", "replace_dish", "add_dish") and session.current_plan:
            pool = await asyncio.to_thread(
                candidate_pool, session, query, need_count + 16, True)
        else:
            pool = await pool_task
            pool = filter_by_meal(pool, session.meal, need_count + 8)
        # 粗检索与槽位抽取并行，本轮新提的设备限制未进检索过滤 → 池口统一补滤
        if session.banned_methods:
            pool = [r for r in pool
                    if not dlg.method_violations(r, session.banned_methods)] or pool
        if replace_targets:
            soup_needed = None  # 替换模式不强制汤
        elif slots.intent == "add_dish":
            soup_needed = True if dlg.rule_slots(message).soup_needed else None
        else:
            soup_needed = True if (session.meal or "晚餐") in ("午餐", "晚餐") else False
        picked = await llm_select(session, message, pool, need_count, soup_needed,
                                  avoid_ids=avoid_ids or None, avoid_hard=avoid_hard)
        new_items = finalize_plan(session, picked, need_count, soup_needed, pool=pool)

        # 5) 合入会话方案（保留 locked；新菜去重合入，数量缺口从池中补位）
        if replace_targets or slots.intent in ("add_constraint", "replace_dish", "add_dish"):
            kept_items = [d for d in session.current_plan if d.recipe_id in kept_ids]
            existing = {d.recipe_id for d in kept_items}
            for item in new_items:
                if item.recipe_id not in existing:
                    kept_items.append(item)
                    existing.add(item.recipe_id)
            # 替换导致数量缺口 → 从池中补齐
            want_total = session.dish_count or max(
                len(kept_items) + need_count,
                planner.default_dish_count(session.people, meal=session.meal),
            )
            for r in pool:
                if len(kept_items) >= want_total:
                    break
                if r.id in existing or not session.constraints.ok(r):
                    continue
                kept_items.append(dlg.DishPlanItem(
                    recipe_id=r.id, name=r.name,
                    role="荤菜" if r.category["meat_type"] == "荤" else "素菜",
                    reason="补齐荤素与口味搭配，均衡本餐营养",
                ))
                existing.add(r.id)
            # 本轮菜数要求少于现有方案 → 按最新需求裁剪（已确认菜品优先保留）
            if session.dish_count and len(kept_items) > session.dish_count:
                kept_items.sort(key=lambda d: not d.locked)
                kept_items = kept_items[: session.dish_count]
            session.current_plan = kept_items
        else:
            session.current_plan = new_items
        # 跨轮去重：本轮方案入账，后续（含 reject_all 换桌）不再重复推荐
        session.record_served()

        # 6) 营养 + 结构化方案事件（评测/UI 消费）
        nutri, balance = plan_nutrition(session)
        plan_payload = self._plan_payload(session, balance)
        yield emit({"type": "plan", **plan_payload})

        # 7) 流式生成说明（前导句已在选菜前输出；说明仅服务文本流/评测协议，
        #    UI 已卡片化，限 150 字压尾部延迟）
        expl = self._explanation_prompt(session, message, balance, kept_ids, replace_targets)
        got_text = False
        reply_parts: list[str] = []
        try:
            async for tok in self.llm.chat_stream(expl, max_tokens=400):
                got_text = True
                reply_parts.append(tok)
                yield emit({"type": "delta", "text": tok})
        except Exception as e:
            print(f"[agent] 说明生成异常，模板兜底: {e}")
        if not got_text:
            fallback = self._template_reply(session, replace_targets)
            for tok in [fallback[i:i + 8] for i in range(0, len(fallback), 8)]:
                yield emit({"type": "delta", "text": tok})
            reply_parts.append(fallback)
        session.add_history("assistant", "".join(reply_parts)[:600])
        yield self._done(t0, first_ts, store, plan=plan_payload)

    async def _clarify_stream(self, session: dlg.DialogSession, q: str, emit,
                              t0: float, first_ts: list[float], store):
        """澄清回合：下发 clarify 事件并流式回显问题。"""
        session.add_history("assistant", q)
        yield emit({"type": "clarify", "question": q})
        for tok in [q[i:i + 6] for i in range(0, len(q), 6)]:
            yield {"type": "delta", "text": tok}
            await asyncio.sleep(0.01)
        yield self._done(t0, first_ts, store)

    # ---------------------------------------------------------------- 提示词
    def _explanation_prompt(
        self, session: dlg.DialogSession, message: str, balance: dict,
        kept_ids: set[int], replaced: list[dlg.DishPlanItem],
    ) -> list[dict]:
        store = get_store()
        dish_lines = []
        for d in session.current_plan:
            rec = store.get(d.recipe_id)
            tags = "、".join(rec.tag_list[:8]) if rec else ""
            ings = "、".join(i.name for i in rec.ingredients[:8]) if rec else ""
            mark = "（沿用上轮，用户已确认）" if d.recipe_id in kept_ids and d.locked else ""
            method = rec.category.get("cook_method", "") if rec else ""
            first_step = (rec.steps[0][:40] + "…") if rec and rec.steps else ""
            dish_lines.append(
                f"- {d.name}［{d.role}］{mark}｜食材：{ings}｜特点：{tags}｜"
                f"做法概要：{method}{'，' if method and first_step else ''}{first_step}｜理由：{d.reason}")
        profile_lines = [profile_text(pid) for pid in session.profile_ids]
        replaced_note = ""
        if replaced:
            replaced_note = "本轮替换掉的菜：" + "、".join(d.name for d in replaced) + "（因新约束不满足，其余菜品保持不变=最小化修改）"
        kept_items = [d for d in session.current_plan if d.locked]
        nut = balance["intake"]
        tastes = [t for t in (session.constraints.taste_pref or "").split("、") if t]
        taste_note = (
            f"口味说明：需同时照顾「{'」「'.join(tastes)}」的不同偏好，"
            "请在对应菜品的理由中点明各照顾谁" if len(tastes) > 1 else "")
        rules = [
            "1. 只能提及方案中列出的菜品，严禁编造菜名或食材",
            "2. 每道菜一句话理由：结合用餐人档案（健康需求/忌口/口味/人数/餐次）说明为什么选它，"
            "并带一句做法概要（参考给定做法概要，烹饪方式+关键步骤，不得编造步骤）",
            "3. 营养数据必须引用给定数值，不要自己计算",
            "4. 150字以内，语气亲切专业，结尾给一句贴心提示",
        ]
        if kept_items:
            rules.append("5. 标注「沿用上轮」的菜品是应最小化修改原则保留的，需说明保留原因；未标注的为本轮新选")
        else:
            rules.append("5. 本方案为全新推荐，所有菜品均为本轮所选，不存在沿用上轮的菜品，请勿提及「沿用」")
        sys_prompt = (
            "你是方太健康膳食助手（专业营养师）。根据给定方案与数据写中文推荐说明。硬性规则：\n"
            + "\n".join(rules)
        )
        user_prompt = "\n".join(filter(None, [
            "用餐人档案：\n" + "\n".join(profile_lines) if profile_lines else "",
            f"硬约束（必须全部满足，方案已通过校验）：{session.constraints.summary()}",
            taste_note,
            f"本轮用户需求：{message}",
            replaced_note,
            f"最终方案（{session.people}人{session.meal or ''}）：\n" + "\n".join(dish_lines),
            f"每人预计营养摄入：热量{nut['kcal']}kcal、蛋白质{nut['protein_g']}g、"
            f"脂肪{nut['fat_g']}g、碳水{nut['carbs_g']}g（{balance['purine_level']}，"
            f"热量占参考值约{100 + balance['deviation_pct']['kcal']:.0f}%偏差）",
            "请输出本餐推荐说明。",
        ]))
        return [self.llm.system(sys_prompt), self.llm.user(user_prompt)]

    def _template_reply(self, session: dlg.DialogSession, replaced: list[dlg.DishPlanItem]) -> str:
        lines = [f"为您搭配了 {len(session.current_plan)} 道菜："]
        for d in session.current_plan:
            mark = "（沿用）" if d.locked else ""
            lines.append(f"· {d.name}［{d.role}］{d.reason} {mark}")
        if replaced:
            lines.append(f"已按您的新要求替换：{'、'.join(x.name for x in replaced)}")
        lines.append(f"已确保满足：{session.constraints.summary()}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 辅助
    def _plan_payload(self, session: dlg.DialogSession, balance: dict) -> dict:
        store = get_store()
        dishes = []
        for d in session.current_plan:
            rec = store.get(d.recipe_id)
            dishes.append({
                "id": d.recipe_id, "name": d.name, "role": d.role,
                "reason": d.reason, "kept": d.locked,
                "ingredients": [i.name for i in rec.ingredients][:8] if rec else [],
                # 前端点击菜名弹窗：全量配料含用量 + 完整做法步骤
                "detail": self._dish_detail(rec),
            })
        return {
            "meal": session.meal, "people": session.people,
            "dishes": dishes,
            "nutrition_per_person": balance,
            "constraints_applied": session.constraints.summary(),
            # 标题栏"忌口"展示用：过敏+疾病忌口+会话忌口合并
            "taboos": sorted(set(session.constraints.allergens)
                             | set(session.constraints.taboo_keys)
                             | set(session.constraints.extra_banned)),
        }

    @staticmethod
    def _dish_detail(rec) -> dict | None:
        """菜品详情（前端点击菜名弹窗用）：全量配料含用量 + 完整做法步骤。"""
        if rec is None:
            return None
        ings = []
        for i in rec.ingredients:
            if i.count:
                c = int(i.count) if float(i.count).is_integer() else i.count
                qty = f"{c}{i.unit or ''}"
            elif i.grams:
                g = int(i.grams) if float(i.grams).is_integer() else round(i.grams, 1)
                qty = f"{g}g"
            else:
                qty = "适量"
            ings.append(f"{i.name} {qty}")
        return {"ingredients": ings, "steps": list(rec.steps)}

    async def dish_reason(self, recipe_id: int,
                          session: dlg.DialogSession | None = None) -> str:
        """为单道菜生成 20 字左右推荐理由（选菜 LLM 偶发漏写时由前端补调）。"""
        store = get_store()
        rec = store.get(recipe_id)
        if rec is None:
            raise ValueError(f"菜品不存在：{recipe_id}")
        bits = [f"菜品：{rec.name}",
                f"食材：{'、'.join(i.name for i in rec.ingredients[:8])}"]
        tags = [t for v in rec.tags.values() for t in v][:8]
        if tags:
            bits.append(f"特点：{'、'.join(tags)}")
        if session is not None:
            if session.meal:
                bits.append(f"餐次：{session.meal}")
            if session.constraints.taste_pref:
                bits.append(f"用户口味偏好：{session.constraints.taste_pref}")
            banned = (session.constraints.allergens + session.constraints.taboo_keys
                      + session.constraints.extra_banned)
            if banned:
                bits.append("用户忌口（理由中不得出现相关口味，如忌辣不得写辣/酸辣）："
                            + "、".join(banned))
        try:
            llm = get_llm()
            txt = (await llm.chat_fast(
                [llm.system(REASON_SYS), llm.user("\n".join(bits))])).strip()
            txt = txt.strip('"“”‘’').rstrip("。。.!！").strip()
            if txt:
                return txt[:40]
        except Exception as e:
            print(f"[agent] 菜品理由生成异常({rec.name}): {e}")
        return f"{rec.name}与本餐荤素搭配协调，营养均衡"

    def _preamble(self, session: dlg.DialogSession, slots: dlg.TurnSlots,
                  replaced=None, no_change: bool = False) -> str:
        """选菜完成前的即时确认句（首 Token 提前；同时回显理解到的约束）。"""
        meal = session.meal or "餐食"
        if no_change:
            return "好的，已校验当前方案均满足您的新要求，无需调整，为您保持不变～\n\n"
        bits = [f"好的，为您安排{session.people}人{meal}"]
        if replaced:
            bits.append(f"将替换 {len(replaced)} 道不合规菜品，其余保留")
        if session.constraints.taste_pref:
            bits.append(f"口味{session.constraints.taste_pref}")
        if session.time_limit_min:
            bits.append(f"{session.time_limit_min}分钟内")
        return "，".join(bits) + "。以下是本餐方案：\n\n"

    def _done(self, t0: float, first_ts: list[float], store, plan: dict | None = None) -> dict:
        return {
            "type": "done",
            "stats": {
                "first_event_s": round(first_ts[0], 3) if first_ts else None,
                "total_s": round(time.perf_counter() - t0, 3),
                "recipes_total": len(store.recipes),
            },
            "plan": plan,
        }


_agent: MealAgent | None = None


def get_agent() -> MealAgent:
    global _agent
    if _agent is None:
        _agent = MealAgent()
    return _agent
