"""自动评测 harness（对齐赛题验收要求）。

指标：
1. 基础推荐：过敏/忌口零违反（每违反一项记录）、菜谱存在性（不得幻觉）
2. 复杂组合：多人约束满足、荤素比/烹饪方式多样性、按人营养（plan 事件携带）
3. 多轮交互：上下文一致性（历史约束不被遗忘）、每轮响应
4. 性能：首 Token 延迟（首个 delta）、单轮端到端、多轮平均
   优秀档: 首Token<2s 单轮<8s 多轮均值<6s；合格档: <5s/<15s/<12s

用法：
  py -3 scripts/eval.py                     # 进程内直跑 20 用例
  py -3 scripts/eval.py --api http://127.0.0.1:8000   # 经 HTTP API 跑
  py -3 scripts/eval.py --case 20           # 只跑指定用例
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.core import dialog as dlg  # noqa: E402
from app.core.constraint_engine import ProfileConstraints, TASTE_PREF_MAP  # noqa: E402
from app.core.profiles import load_profiles, get_profile  # noqa: E402
from app.core.recipe_store import get_store  # noqa: E402

# 档位阈值
EXCELLENT = {"first_token": 2.0, "turn_e2e": 8.0, "multi_avg": 6.0}
PASS = {"first_token": 5.0, "turn_e2e": 15.0, "multi_avg": 12.0}

# ---------------------------------------------------------------- 冲突场景（对话追加约束 vs 档案人设）
# 验证优先级：对话最新追加的口味/忌口 覆盖 档案口味偏好与先前会话忌口；
# 档案过敏/疾病忌口属健康安全约束，任何情况下不得被对话解除（赛题零违反口径）。
CONFLICT_CASES = [
    {"id": 901, "note": "档案口味清淡 vs 对话要辣",
     "profile_id": 3,    # user3：高血压高血糖、花生过敏、口味清淡（疾病忌口不拦辣）
     "user_messages": ["帮我想顿晚饭。", "今天想换换口味，来点辣的。"],
     "expect": "第2轮推荐应含辣菜（遵循对话而非档案'清淡'），且花生过敏零违反"},
    {"id": 902, "note": "会话先禁辣 vs 后要辣（最新对话优先）",
     "profile_id": 37,   # user37：备孕、鸡蛋过敏、口味中性
     "user_messages": ["安排一顿晚饭，别做辣的。", "还是来点辣的吧，太清淡吃不惯。"],
     "expect": "第2轮'辣'会话忌口应被解除并推荐辣菜，且鸡蛋过敏零违反"},
]


def track_session_constraints(session_banned: list[str], dialog_taste: str | None,
                              msg: str) -> tuple[list[str], str | None]:
    """按消息更新会话累积追加忌口与最新正向口味（纯规则，与 dialog.rule_slots 同源）。

    冲突解决与 dialog.apply_slots 一致：本轮正向口味解除先前累积的同词忌口
    （最新对话优先）；同轮既禁又要的词保守保留（多人口味分歧）。
    """
    slots = dlg.rule_slots(msg)
    for b in slots.banned_add:
        if b and b not in session_banned:
            session_banned.append(b)
    conflicted = set(slots.banned_add)
    for t in slots.taste_add:
        canon = TASTE_PREF_MAP.get(t, t)
        if not canon:
            continue
        if t in session_banned and t not in conflicted:
            session_banned.remove(t)
        dialog_taste = canon
        break
    return session_banned, dialog_taste


def effective_constraints(profile: dict, session_banned: list[str]) -> ProfileConstraints:
    """有效校验约束 = 档案约束 + 会话累积追加忌口（过敏/疾病忌口天然保留自档案）。"""
    eff = ProfileConstraints.from_profile(profile)
    if session_banned:
        eff.add_banned(session_banned)
    return eff


def taste_follow_check(plan_ev, dialog_taste, profile_taste, store) -> dict | None:
    """对话口味与档案口味冲突时，检查本轮方案是否体现对话口味（而非档案口味）。"""
    if not dialog_taste or dialog_taste == profile_taste:
        return None
    hit = False
    for d in (plan_ev or {}).get("dishes", []):
        rec = store.get(d["id"])
        text = rec.name if rec else str(d.get("name", ""))
        tags = set(rec.tags.get("口味", [])) if rec else set()
        if any(dialog_taste in t for t in tags) or dialog_taste in text:
            hit = True
            break
    return {"dialog_taste": dialog_taste, "profile_taste": profile_taste,
            "followed": "dialog" if hit else "profile"}


# ---------------------------------------------------------------- in-process 驱动
async def run_turn_inproc(agent, session, message: str) -> dict:
    events = []
    t0 = time.perf_counter()
    first_token = None
    plan_ev = None
    async for ev in agent.stream_chat(session, message):
        if ev.get("type") == "delta" and first_token is None:
            first_token = time.perf_counter() - t0
        if ev.get("type") == "plan":
            plan_ev = ev
        events.append(ev)
    total = time.perf_counter() - t0
    text = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    return {
        "first_token_s": round(first_token, 3) if first_token else None,
        "total_s": round(total, 3),
        "plan": plan_ev,
        "reply_len": len(text),
    }


# ---------------------------------------------------------------- HTTP 驱动
async def run_turn_http(client, base, session_id, message, profile_ids) -> tuple[dict, str]:
    """HTTP 模式：返回 (指标, 会话id)。"""
    t0 = time.perf_counter()
    first_token = None
    plan_ev = None
    text_parts = []
    sid = session_id
    async with client.stream(
        "POST", f"{base}/api/chat",
        json={"message": message, "session_id": session_id, "profile_ids": profile_ids},
        timeout=120,
    ) as resp:
        sid = resp.headers.get("X-Session-Id", session_id)
        buf = ""
        async for chunk in resp.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                if not raw.startswith("data: "):
                    continue
                ev = json.loads(raw[6:])
                if ev.get("type") == "delta":
                    if first_token is None:
                        first_token = time.perf_counter() - t0
                    text_parts.append(ev.get("text", ""))
                elif ev.get("type") == "plan":
                    plan_ev = ev
    return {
        "first_token_s": round(first_token, 3) if first_token else None,
        "total_s": round(time.perf_counter() - t0, 3),
        "plan": plan_ev,
        "reply_len": len("".join(text_parts)),
    }, sid


# ---------------------------------------------------------------- 校验
def validate_plan(plan_ev: dict | None, constraints: ProfileConstraints, store) -> dict:
    result = {"violations": [], "hallucinated": [], "dish_count": 0, "ok": True}
    if not plan_ev:
        return result
    dishes = plan_ev.get("dishes", [])
    result["dish_count"] = len(dishes)
    for d in dishes:
        recs = store.find_by_name(d["name"])
        if not recs or d["id"] not in {r.id for r in recs}:
            result["hallucinated"].append({"id": d.get("id"), "name": d.get("name")})
        else:
            rec = store.get(d["id"]) or recs[0]
            vs = constraints.violations_of(rec)
            if vs:
                result["violations"].append({"dish": d["name"], "hits": vs})
    result["ok"] = not result["violations"] and not result["hallucinated"]
    return result


# ---------------------------------------------------------------- 主流程
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="", help="HTTP API 基址（缺省进程内直跑）")
    ap.add_argument("--case", type=int, default=0, help="只跑指定用例 id")
    ap.add_argument("--profile", type=int, default=0, help="固定档案 id（缺省按用例轮转）")
    ap.add_argument("--out", default="eval_report.json")
    ap.add_argument("--skip-conflict", action="store_true",
                    help="跳过内置冲突场景（对话追加约束 vs 档案人设）")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    store = get_store()
    profiles = load_profiles()
    with open(config.DATA_DIR / "对话用例.json", encoding="utf-8") as f:
        cases = json.load(f)
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]

    agent = None
    http_client = None
    if args.api:
        import httpx
        http_client = httpx.AsyncClient()
    else:
        from app.core.agent import get_agent
        agent = get_agent()

    # 用例 → 档案分配：覆盖多样人群（孕妇/慢病/过敏/增肌等）
    profile_cycle = [3, 2, 17, 16, 1, 6, 24, 30, 37, 19,
                     8, 22, 5, 13, 26, 10, 28, 18, 4, 50]

    all_turns = []
    report_cases = []
    for case in cases:
        pid = args.profile or profile_cycle[(case["id"] - 1) % len(profile_cycle)]
        profile = profiles[pid]
        profile_taste = ProfileConstraints.from_profile(profile).taste_pref
        session = dlg.DialogSession()
        session.set_profiles([pid], get_profile)
        http_sid = None
        case_turns = []
        session_banned: list[str] = []      # 会话累积追加忌口（含后续解除）
        dialog_taste: str | None = None     # 最新正向口味（对话优先于档案）
        for i, msg in enumerate(case["user_messages"]):
            session_banned, dialog_taste = track_session_constraints(
                session_banned, dialog_taste, msg)
            eff = effective_constraints(profile, session_banned)
            if args.api:
                r, http_sid = await run_turn_http(
                    http_client, args.api, http_sid, msg, [pid])
            else:
                r = await run_turn_inproc(agent, session, msg)
            v = validate_plan(r["plan"], eff, store)
            r["turn"] = i + 1
            r["profile_id"] = pid
            r["message"] = msg[:30]
            r["session_banned"] = list(session_banned)
            r["validation"] = v
            tc = taste_follow_check(r["plan"], dialog_taste, profile_taste, store)
            if tc:
                r["taste_conflict"] = tc
            case_turns.append(r)
            all_turns.append(r)
            status = "OK" if v["ok"] else "VIOLATION"
            ft = r["first_token_s"]
            print(f"  用例{case['id']}-轮{i+1} [{status}] 首Token={ft}s 总={r['total_s']}s "
                  f"菜品={v['dish_count']} 荤素比={(r['plan'] or {}).get('nutrition_per_person', {}).get('intake', {}).get('kcal', '-')}kcal/人")
            if session_banned:
                print(f"    会话忌口(累积): {session_banned}")
            if tc:
                print(f"    口味冲突: 对话要[{tc['dialog_taste']}] vs 档案[{tc['profile_taste']}] "
                      f"→ 实际遵循: {tc['followed']}")
            if v["violations"]:
                print(f"    !! 违反: {json.dumps(v['violations'], ensure_ascii=False)[:200]}")
            if v["hallucinated"]:
                print(f"    !! 幻觉菜名: {v['hallucinated']}")
        # 多轮平均
        if case_turns:
            avg = statistics.mean(t["total_s"] for t in case_turns)
            report_cases.append({
                "case_id": case["id"], "profile_id": pid,
                "turns": case_turns, "case_avg_s": round(avg, 3),
            })

    # ---------------- 冲突场景：对话追加约束 vs 档案人设 ----------------
    conflict_results = []
    if not args.skip_conflict:
        print("\n冲突场景（对话追加约束 vs 档案人设，对话优先；过敏/疾病忌口不可解除）:")
        for cc in CONFLICT_CASES:
            pid = cc["profile_id"]
            profile = profiles[pid]
            profile_taste = ProfileConstraints.from_profile(profile).taste_pref
            session = dlg.DialogSession()
            session.set_profiles([pid], get_profile)
            http_sid = None
            session_banned, dialog_taste = [], None
            turns = []
            for i, msg in enumerate(cc["user_messages"]):
                session_banned, dialog_taste = track_session_constraints(
                    session_banned, dialog_taste, msg)
                eff = effective_constraints(profile, session_banned)
                if args.api:
                    r, http_sid = await run_turn_http(
                        http_client, args.api, http_sid, msg, [pid])
                else:
                    r = await run_turn_inproc(agent, session, msg)
                v = validate_plan(r["plan"], eff, store)
                tc = taste_follow_check(r["plan"], dialog_taste, profile_taste, store)
                turns.append({
                    "turn": i + 1, "message": msg[:40],
                    "session_banned": list(session_banned),
                    "dishes": [d["name"] for d in (r["plan"] or {}).get("dishes", [])],
                    "validation": v, "taste_conflict": tc,
                })
            last = turns[-1]
            followed = (last.get("taste_conflict") or {}).get("followed")
            ok = followed == "dialog" and all(t["validation"]["ok"] for t in turns)
            conflict_results.append({
                "case_id": cc["id"], "note": cc["note"], "expect": cc["expect"],
                "pass": ok, "turns": turns,
            })
            print(f"  冲突{cc['id']} [{cc['note']}] {'PASS' if ok else 'FAIL'} "
                  f"(末轮遵循: {followed}, 零违反: {all(t['validation']['ok'] for t in turns)})")
            for t in turns:
                if not t["validation"]["ok"]:
                    print(f"    !! 轮{t['turn']} 违反: "
                          f"{json.dumps(t['validation']['violations'], ensure_ascii=False)[:200]}")

    # 汇总
    def agg(key):
        vals = [t[key] for t in all_turns if t.get(key) is not None]
        return {
            "mean": round(statistics.mean(vals), 3) if vals else None,
            "p95": round(sorted(vals)[int(len(vals) * 0.95) - 1], 3) if vals else None,
            "max": round(max(vals), 3) if vals else None,
        }

    first_tok = agg("first_token_s")
    e2e = agg("total_s")
    case_avgs = [c["case_avg_s"] for c in report_cases]
    profile_viol = 0      # 档案过敏/疾病忌口违反（赛题零违反口径）
    session_viol = 0      # 会话追加忌口违反（多轮上下文一致性）
    for t in all_turns:
        for v in t["validation"]["violations"]:
            if str(v.get("source", "")).startswith("会话忌口"):
                session_viol += 1
            else:
                profile_viol += 1
    hallu_total = sum(len(t["validation"]["hallucinated"]) for t in all_turns)
    tc_turns = [t["taste_conflict"] for t in all_turns if t.get("taste_conflict")]

    def grade(val, key):
        if val is None:
            return "n/a"
        if val <= EXCELLENT[key]:
            return "优秀"
        if val <= PASS[key]:
            return "合格"
        return "不合格"

    summary = {
        "turns_total": len(all_turns),
        "first_token_s": first_tok, "first_token_grade": grade(first_tok["mean"], "first_token"),
        "turn_e2e_s": e2e, "turn_e2e_grade": grade(e2e["mean"], "turn_e2e"),
        "multi_avg_s": {"mean": round(statistics.mean(case_avgs), 3) if case_avgs else None},
        "multi_avg_grade": grade(statistics.mean(case_avgs) if case_avgs else None, "multi_avg"),
        "allergen_violations": profile_viol,
        "session_banned_violations": session_viol,
        "hallucinated_dishes": hallu_total,
        "zero_violation_pass_rate": round(
            sum(1 for t in all_turns if t["validation"]["ok"]) / len(all_turns) * 100, 1
        ) if all_turns else 0,
        "session_constraint_checks": {
            "turns_with_session_banned": sum(1 for t in all_turns if t.get("session_banned")),
            "taste_conflict_turns": len(tc_turns),
            "taste_followed_dialog": sum(1 for x in tc_turns if x["followed"] == "dialog"),
        },
    }
    print("\n" + "=" * 60)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if conflict_results:
        passed = sum(1 for c in conflict_results if c["pass"])
        print(f"冲突场景: {passed}/{len(conflict_results)} 通过"
              f"（对话追加约束优先于档案人设，过敏/疾病忌口不可解除）")
    out = {"summary": summary, "cases": report_cases,
           "conflict_scenarios": conflict_results,
           "thresholds": {"excellent": EXCELLENT, "pass": PASS}}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入 {args.out}")
    if http_client:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
