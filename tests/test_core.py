"""核心模块单元测试：数据解析 / 约束引擎 / 营养估算 / 组合规划。

运行：py -3 -m pytest tests/ -v （或 py -3 tests/test_core.py）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.recipe_store import (
    get_store, parse_ingredients, parse_label_field, estimate_minutes,
)
from app.core.constraint_engine import ProfileConstraints, merge_constraints
from app.core.nutrition import recipe_nutrition, balance_report
from app.core.profiles import load_profiles
from app.core.planner import build_combo, default_dish_count
from app.core import dialog as dlg
from app.core.agent import filter_by_meal, meal_conflict_ok, MEAL_CONFLICTS
from app.core.recipe_store import Recipe


def test_recipe_store():
    store = get_store()
    assert len(store.recipes) == 2000
    # 标签规范化
    tags = parse_label_field("午餐、晚餐、湘菜、中秋节、哺乳、鲜美")
    assert "川湘菜" in tags["菜系"] and "湘菜" not in tags["菜系"]
    assert "中秋节" in tags["节日"] and "哺乳期" in tags["人群"]
    assert "鲜" in tags["口味"]
    # 噪声标签剔除
    tags2 = parse_label_field("根据菜谱内容【花生芝麻粥】，我们建议添加一个新标签、午餐")
    assert "午餐" in tags2["餐次"] and len(tags2["餐次"]) == 1
    # 食材解析
    ings = parse_ingredients("主料：鲈鱼600克（约1条）；辅料：盐2克；葱段10g；冰糖30g（（压碎））")
    assert ings[0].name == "鲈鱼" and ings[0].grams == 600
    assert any(i.name == "葱段" and i.grams == 10 for i in ings)
    # 存在性
    assert store.exists("家常鲈鱼")
    assert not store.exists("不存在的菜名123")
    # 同名多配方
    recs = store.find_by_name("小吊梨汤")
    assert len(recs) >= 1
    print("test_recipe_store ✓")


def test_constraint_engine():
    profiles = load_profiles()
    store = get_store()
    # user1 海鲜过敏：含鱼菜谱必须违规
    c1 = ProfileConstraints.from_profile(profiles[1])
    fish = store.find_by_name("家常鲈鱼")[0]
    assert not c1.ok(fish)
    vs = c1.violations_of(fish)
    assert any(v["source"] == "海鲜" for v in vs)
    # user6 哺乳期+海鲜鸡蛋过敏
    c6 = ProfileConstraints.from_profile(profiles[6])
    assert "哺乳期" in c6.taboo_keys and "海鲜" in c6.allergens
    # 高尿酸忌口：炖汤类海鲜
    c5 = ProfileConstraints.from_profile(profiles[5])  # 高尿酸+高血压
    assert "高尿酸" in c5.taboo_keys
    # 多人合并
    merged = merge_constraints([c1, c6])
    assert set(merged.allergens) >= {"海鲜", "鸡蛋"}
    # 会话追加忌口
    c1.add_banned(["辣"])
    rec = store.find_by_name("蒜香炒花甲")[0]
    # 花甲本就被海鲜过敏拦截
    assert not c1.ok(rec)
    print("test_constraint_engine ✓")


def test_nutrition():
    store = get_store()
    rec = store.find_by_name("家常鲈鱼")[0]
    n = recipe_nutrition(rec, servings=2.0)
    assert 50 < n.kcal < 600, f"鲈鱼每人热量异常: {n.kcal}"
    assert n.protein > 10
    report = balance_report(n, "中")
    assert "deviation_pct" in report and "intake" in report
    print(f"test_nutrition ✓ (家常鲈鱼每人 {n.kcal:.0f}kcal, 蛋白 {n.protein:.0f}g, {n.purine_level()})")


def test_planner():
    store = get_store()
    # 用前 60 道菜做候选池
    combo = build_combo(store.recipes[:60], people=4, dish_count=5)
    assert len(combo.dishes) == 5
    assert "荤素比" in combo.diversity
    # 四菜一汤默认含汤（若池中有）
    assert default_dish_count(6) == 5
    print(f"test_planner ✓ (5菜组合: {combo.diversity})")


def test_dialog_rules():
    s = dlg.rule_slots("帮我想顿晚饭")
    assert s.intent == "new_request"
    s2 = dlg.rule_slots("别做辣的，口味清淡一点")
    assert "辣" in s2.banned_add or s2.intent == "add_constraint"
    s3 = dlg.rule_slots("我今天下班会比较晚，想做个半小时内能搞定的晚饭")
    assert s3.time_limit_min == 30 and s3.meal == "晚餐"
    s4 = dlg.rule_slots("做个四菜一汤，营养均衡一点的")
    assert s4.dish_count == 5 and s4.soup_needed  # 四菜一汤 = 4菜+1汤=5道
    s5 = dlg.rule_slots("晚上两个人吃")
    assert s5.people == 2
    s6 = dlg.rule_slots("周末想请几个人来家里吃饭")
    assert s6.intent == "banquet"
    print("test_dialog_rules ✓")


def test_estimate_minutes():
    store = get_store()
    fast = [r for r in store.recipes if estimate_minutes(r) <= 30]
    assert 0 < len(fast) < 2000
    print(f"test_estimate_minutes ✓ ({len(fast)} 道菜 30 分钟内)")


def _meal_recipe(rid: int, name: str, meals: list[str]) -> Recipe:
    return Recipe(id=rid, name=name, tags={"餐次": meals})


def test_meal_conflict_filter():
    # 冲突映射：晚餐/午餐排除早餐，早餐排除午餐晚餐
    assert MEAL_CONFLICTS["晚餐"] == {"早餐"}
    assert "早餐" in MEAL_CONFLICTS["早餐"] or MEAL_CONFLICTS["早餐"] >= {"午餐", "晚餐"}
    # 兼容判定：命中目标餐次 ✓、无标签中性 ✓、明确冲突 ✗
    dinner = _meal_recipe(1, "红烧排骨", ["晚餐", "午餐"])
    neutral = _meal_recipe(2, "清炒时蔬", [])
    breakfast = _meal_recipe(3, "小米粥", ["早餐"])
    assert meal_conflict_ok(dinner, "晚餐")
    assert meal_conflict_ok(neutral, "晚餐")
    assert not meal_conflict_ok(breakfast, "晚餐")
    assert meal_conflict_ok(breakfast, "早餐")
    assert meal_conflict_ok(dinner, None)
    # 后过滤：晚餐池剔除早餐菜，保留中性菜
    pool = [breakfast, dinner, neutral, _meal_recipe(4, "清蒸鱼", ["晚餐"]),
            _meal_recipe(5, "番茄炒蛋", ["午餐"]), _meal_recipe(6, "凉拌黄瓜", ["晚餐"])]
    kept = filter_by_meal(pool, "晚餐", need=4)
    assert breakfast not in kept and dinner in kept and neutral in kept
    # 剔除后严重不足（全是不兼容菜）→ 回退原池
    scarce = [breakfast, _meal_recipe(7, "白粥", ["早餐"]), _meal_recipe(8, "豆浆", ["早餐"]),
              _meal_recipe(9, "油条", ["早餐"]), _meal_recipe(10, "煎蛋", ["早餐"])]
    assert filter_by_meal(scarce, "晚餐", need=4) == scarce
    print("test_meal_conflict_filter ✓")


def test_conflict_and_taste():
    # 矛盾检测：消息内自相矛盾
    slots = dlg.rule_slots("想吃辣的但是别放辣")
    assert slots.taste_add and slots.banned_add
    c = dlg.ProfileConstraints()
    assert dlg.detect_conflicts(slots, c)
    # 跨轮矛盾：此前忌辣，本轮想吃辣
    c.add_banned(["辣"])
    slots2 = dlg.TurnSlots(taste_add=["辣"])
    assert "辣" in dlg.detect_conflicts(slots2, c)
    # 正向口味切换不算矛盾
    slots3 = dlg.TurnSlots(taste_add=["清淡"])
    assert not dlg.detect_conflicts(slots3, c)
    # 口味多值并存：先辣后清淡，两个都保留
    sess = dlg.DialogSession()
    sess.apply_slots(dlg.TurnSlots(taste_add=["辣"]))
    sess.apply_slots(dlg.TurnSlots(taste_add=["清淡"]))
    assert "辣" in (sess.constraints.taste_pref or "") and "清淡" in (sess.constraints.taste_pref or "")
    # 多人合并：不同人口味都保留（顿号多值）
    c1 = dlg.ProfileConstraints()
    c1.taste_pref = "辣"
    c2 = dlg.ProfileConstraints()
    c2.taste_pref = "清淡"
    merged = merge_constraints([c1, c2])
    assert "辣" in (merged.taste_pref or "") and "清淡" in (merged.taste_pref or "")
    print("test_conflict_and_taste ✓")


if __name__ == "__main__":
    test_recipe_store()
    test_constraint_engine()
    test_nutrition()
    test_planner()
    test_dialog_rules()
    test_estimate_minutes()
    test_meal_conflict_filter()
    test_conflict_and_taste()
    print("\n全部通过 ✓")
