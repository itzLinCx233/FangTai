"""硬约束引擎：过敏原/疾病忌口过滤 + 双遍校验。

设计对应评分项①（基础推荐 20 分：忌口/过敏零违反）：
1. 检索前置过滤：违禁菜谱在候选池阶段即被剔除；
2. 生成后校验：对 LLM 输出方案再做食材级扫描，违规自动替换。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.recipe_store import Recipe

# ---------------------------------------------------------------- 过敏原映射
ALLERGEN_ALIASES: dict[str, list[str]] = {
    "海鲜": [
        "虾", "蟹", "龙虾", "蛤", "花甲", "花蛤", "蛏", "扇贝", "鲍鱼", "牡蛎", "海蛎",
        "生蚝", "蚝", "鱿鱼", "墨鱼", "章鱼", "带鱼", "黄鱼", "鲈鱼", "鳕鱼", "三文鱼",
        "金枪鱼", "龙利鱼", "巴沙鱼", "银鱼", "鲳鱼", "鲫鱼", "草鱼", "鲤鱼", "黑鱼",
        "泥鳅", "黄鳝", "沙丁鱼", "秋刀鱼", "鱼", "海鲜", "干贝", "瑶柱", "虾皮",
        "虾米", "海米", "蟹黄", "鱼子", "鱼露", "海带", "紫菜", "海苔", "龙井虾仁",
        "鱼丸", "鱼豆腐", "鱼腥草",
    ],
    "虾": ["虾", "基围虾", "虾仁", "虾皮", "虾米", "海米", "龙虾", "虾酱", "虾油"],
    "蟹": ["蟹", "螃蟹", "梭子蟹", "大闸蟹", "蟹黄", "蟹肉", "蟹柳"],
    "鱼": ["鱼", "带鱼", "黄鱼", "鲈鱼", "鳕鱼", "三文鱼", "金枪鱼", "龙利鱼", "巴沙鱼",
          "银鱼", "鲳鱼", "鲫鱼", "草鱼", "鲤鱼", "黑鱼", "泥鳅", "黄鳝", "沙丁鱼",
          "秋刀鱼", "鱼露", "鱼丸", "鱼豆腐"],
    "坚果": ["花生", "杏仁", "核桃", "腰果", "开心果", "松子", "榛子", "夏威夷果",
            "巴旦木", "瓜子", "芝麻", "栗子", "栗仁", "坚果", "花生酱", "芝麻酱",
            "花生油", "杏仁片", "杏仁粉", "核桃仁"],
    "花生": ["花生", "花生酱", "花生油", "花生碎", "花生米"],
    "牛奶": ["牛奶", "纯牛奶", "奶粉", "奶油", "淡奶油", "黄油", "芝士", "奶酪",
            "马苏里拉", "炼乳", "酸奶", "奶昔", "酥油", "乳清", "奶油奶酪", "牛奶片",
            "奶", "奶粉", "奶油芝士", "马斯卡彭"],
    "鸡蛋": ["蛋"],  # 鸡蛋/蛋清/蛋黄/蛋液/皮蛋/鹌鹑蛋/蛋黄酱 等均含"蛋"
    "豆类": ["豆腐", "豆干", "香干", "千张", "腐竹", "豆皮", "素鸡", "豆浆", "豆奶",
            "黄豆", "黑豆", "红豆", "绿豆", "青豆", "豌豆", "蚕豆", "毛豆", "芸豆",
            "鹰嘴豆", "豆芽", "豆瓣酱", "豆豉", "味噌", "豆花", "豆腐皮", "油豆皮",
            "兰芳", "豆沙", "豆苗", "豆苗", "扁豆", "豇豆", "四季豆", "豆渣"],
    "豆制品": ["豆腐", "豆干", "香干", "千张", "腐竹", "豆皮", "素鸡", "豆浆", "豆花",
              "豆腐皮", "油豆皮", "豆奶", "豆瓣酱", "豆豉", "味噌", "黄豆酱油", "豆豉",
              "日本豆腐"],
    "芒果": ["芒果"],
    "啤酒": ["啤酒", "黑啤", "精酿"],
    "酒精": ["啤酒", "黄酒", "白酒", "红酒", "料酒", "米酒", "清酒", "朗姆酒", "酒酿",
            "醪糟", "红酒", "威士忌", "香槟", "酒"],
}


def _alias_normalize(aliases: list[str]) -> list[str]:
    # 长别名优先匹配，避免"虾"抢先短匹配导致定位不准；去重
    return sorted(set(aliases), key=len, reverse=True)


# ---------------------------------------------------------------- 疾病忌口
# 慢性病/特殊人群 → 违禁食材关键词（忌口，比过敏略宽：按食材命中而非整字）
DISEASE_TABOOS: dict[str, list[str]] = {
    "高血糖": ["白砂糖", "冰糖", "红糖", "白糖", "糖粉", "绵白糖", "蜂蜜", "麦芽糖",
              "巧克力", "炼乳", "焦糖"],
    "糖尿病": ["白砂糖", "冰糖", "红糖", "白糖", "糖粉", "绵白糖", "蜂蜜", "麦芽糖",
              "巧克力", "炼乳", "焦糖"],
    "高血压": ["咸肉", "腊肉", "腊肠", "咸菜", "榨菜", "酱菜", "咸鱼", "火腿", "培根",
              "泡菜", "梅干菜", "盐焗"],
    "高尿酸": ["牡蛎", "生蚝", "扇贝", "蛤", "花甲", "花蛤", "蛏", "虾", "蟹", "龙虾",
              "沙丁鱼", "秋刀鱼", "鱼子", "蟹黄", "浓汤", "肉汤", "高汤", "骨汤",
              "肝", "腰", "肚", "脑", "肠", "鸡胗", "鹅肝", "猪肝", "肥肠", "啤酒",
              "香菇", "芦笋", "紫菜", "火锅汤"],
    "痛风": ["牡蛎", "生蚝", "扇贝", "蛤", "花甲", "花蛤", "蛏", "虾", "蟹", "龙虾",
            "沙丁鱼", "秋刀鱼", "鱼子", "蟹黄", "浓汤", "肉汤", "高汤", "骨汤", "肝",
            "腰", "肚", "脑", "肠", "鸡胗", "鹅肝", "猪肝", "肥肠", "啤酒", "香菇",
            "芦笋", "紫菜"],
    "孕妇": ["咖啡", "浓茶", "茶", "啤酒", "红酒", "白酒", "料酒", "酒", "生腌", "刺身",
            "山楂", "薏米", "芦荟", "马齿苋", "蟹", "甲鱼"],
    "哺乳期": ["麦芽", "韭菜", "咖啡", "浓茶", "茶", "啤酒", "红酒", "白酒", "料酒",
              "酒", "薄荷", "花椒", "山楂"],
    "备孕": ["咖啡", "浓茶", "啤酒", "红酒", "白酒", "料酒", "酒"],
}

# 特殊人群档案值 → 疾病忌口键
CROWD_TO_TABOO = {
    "孕妇": "孕妇", "哺乳期": "哺乳期", "哺乳": "哺乳期", "备孕": "备孕",
    "高血压": "高血压", "高血糖": "高血糖", "糖尿病": "糖尿病",
    "高尿酸": "高尿酸", "痛风": "痛风",
}

# ---------------------------------------------------------------- 软偏好（用于检索加权，非硬过滤）
TASTE_PREF_MAP = {
    "偏辣": "辣", "辣味": "辣", "辛辣": "辣", "酸辣": "辣",
    "酸甜": "甜", "偏甜": "甜", "甜食": "甜",
    "偏咸": "咸", "偏酸": "酸", "酸味": "酸",
    "清淡": "清淡", "少油": "清淡", "少盐": "清淡", "少糖": "清淡",
    "无糖": "清淡", "少盐少糖": "清淡", "中性": None,
    "重口味": None, "重油": None,
}

HEALTH_NEED_KEYWORDS: dict[str, list[str]] = {
    "增肌": ["鸡胸", "牛肉", "牛排", "鱼", "虾", "鸡蛋", "豆腐", "牛奶", "瘦肉", "排骨", "蛋白"],
    "补充蛋白质": ["鸡胸", "牛肉", "鱼", "虾", "鸡蛋", "豆腐", "牛奶", "瘦肉", "排骨", "蛋白"],
    "提高精子质量": ["牡蛎", "瘦肉", "鸡蛋", "鱼", "虾", "坚果", "核桃", "锌"],
    "补充锌": ["牡蛎", "瘦肉", "猪肝", "鸡蛋", "核桃"],
    "补钙": ["牛奶", "豆腐", "虾", "芝麻", "奶酪", "芥蓝", "油菜", "小白菜", "带鱼", "酸奶"],
    "补铁": ["猪肝", "牛肉", "瘦肉", "菠菜", "红枣", "木耳", "鸭血", "猪血", "樱桃"],
    "补血": ["猪肝", "牛肉", "瘦肉", "菠菜", "红枣", "木耳", "鸭血", "猪血", "桂圆", "红糖"],
    "补气血": ["红枣", "桂圆", "枸杞", "猪肝", "乌鸡", "红糖", "菠菜", "木耳", "牛肉", "山药"],
    "调理月经": ["红枣", "红糖", "姜", "桂圆", "枸杞", "乌鸡"],
    "养脾胃": ["山药", "小米", "南瓜", "红枣", "粥", "姜", "猴头菇"],
    "补叶酸": ["菠菜", "芦笋", "西兰花", "油菜", "豆", "动物肝", "香蕉"],
    "调理内分泌": ["豆浆", "豆腐", "山药", "当归", "红枣", "枸杞"],
    "调节内分泌": ["豆浆", "豆腐", "山药", "当归", "红枣", "枸杞"],
    "调节激素": ["豆浆", "豆腐", "亚麻籽", "当归"],
    "暖宫": ["姜", "红糖", "红枣", "桂圆", "羊肉", "当归"],
    "养子宫": ["红枣", "桂圆", "枸杞", "乌鸡", "姜"],
    "安胎": ["鱼", "鸡蛋", "牛奶", "绿叶菜", "燕麦"],
    "缓解孕吐": ["姜", "柠檬", "酸梅", "苏打饼干", "土豆", "面包"],
    "催乳": ["鲫鱼", "猪蹄", "花生", "酒酿", "木瓜", "丝瓜", "莴笋", "豆腐", "汤"],
    "降血压": ["芹菜", "木耳", "香蕉", "菠菜", "海带", "冬瓜", "燕麦", "菊"],
    "降压": ["芹菜", "木耳", "香蕉", "菠菜", "海带", "冬瓜", "燕麦", "菊"],
    "控糖": ["燕麦", "荞麦", "苦瓜", "秋葵", "杂粮", "糙米", "西兰花", "豆"],
    "控制血糖": ["燕麦", "荞麦", "苦瓜", "秋葵", "杂粮", "糙米", "西兰花", "豆"],
    "降尿酸": ["冬瓜", "黄瓜", "芹菜", "白菜", "萝卜", "土豆", "蛋", "牛奶"],
    "护心": ["三文鱼", "燕麦", "坚果", "核桃", "深海鱼", "橄榄油", "亚麻籽"],
    "护血管": ["三文鱼", "燕麦", "木耳", "洋葱", "深海鱼", "橄榄油", "山楂"],
    "护肾": ["山药", "冬瓜", "白菜", "萝卜", "蛋", "瘦肉"],
    "护肝": ["枸杞", "菊花", "绿叶菜", "西兰花", "猪肝", "菠菜", "决明子"],
    "护眼": ["胡萝卜", "枸杞", "蓝莓", "猪肝", "菠菜", "南瓜", "玉米"],
    "减脂": ["鸡胸", "西兰花", "虾", "魔芋", "冬瓜", "黄瓜", "生菜", "燕麦", "豆腐"],
    "减重": ["鸡胸", "西兰花", "虾", "魔芋", "冬瓜", "黄瓜", "生菜", "燕麦", "豆腐"],
    "增加体重": ["牛肉", "排骨", "土豆", "米饭", "面", "奶酪", "坚果"],
    "提高耐力": ["牛肉", "红薯", "燕麦", "鸡蛋", "香蕉", "全麦"],
    "提高爆发力": ["牛肉", "鸡蛋", "鸡胸", "米饭", "面"],
    "提高免疫力": ["菌菇", "香菇", "西兰花", "彩椒", "猕猴桃", "橙", "柠檬", "山药"],
    "改善睡眠": ["牛奶", "小米", "百合", "莲子", "酸枣仁", "桂圆"],
    "助眠": ["牛奶", "小米", "百合", "莲子", "酸枣仁", "桂圆"],
    "安神": ["牛奶", "小米", "百合", "莲子", "酸枣仁"],
    "缓解疲劳": ["牛肉", "菠菜", "香蕉", "红枣", "枸杞", "蛋"],
    "改善便秘": ["芹菜", "红薯", "燕麦", "木耳", "火龙果", "酸奶", "韭菜", "玉米"],
    "排毒": ["绿豆", "冬瓜", "木耳", "芹菜", "苦瓜"],
    "消肿": ["冬瓜", "红豆", "鲤鱼", "薏米", "荷叶"],
    "美容养颜": ["银耳", "桃胶", "猪蹄", "红枣", "枸杞", "雪燕", "皂角米"],
    "美容": ["银耳", "桃胶", "猪蹄", "红枣", "枸杞"],
    "改善皮肤": ["银耳", "桃胶", "猪蹄", "红枣", "番茄", "维C"],
    "调理肠胃": ["小米", "山药", "南瓜", "粥", "酸奶"],
    "健胃消食": ["山楂", "麦芽", "陈皮", "萝卜", "山药"],
    "养胃": ["小米", "山药", "南瓜", "粥", "面", "姜", "猴头菇"],
    "均衡营养": [],
    "营养均衡": [],
}

# 健康需求 → 菜谱功效标签映射（检索加权用）
HEALTH_NEED_TAGS: dict[str, list[str]] = {
    "减脂": ["减脂"], "减重": ["减脂"], "改善便秘": ["改善便秘"],
    "补血": ["补血"], "补气血": ["补血"], "补铁": ["补血"],
    "养胃": ["养胃"], "养脾胃": ["养胃"], "健胃消食": ["健胃消食"],
    "改善睡眠": ["助眠"], "助眠": ["助眠"], "安神": ["助眠"],
}


# 会话忌口词 → 同类食材扩展（"不吃猪肉"应同时拦住 排骨/五花肉/里脊 等变体）
SESSION_BANNED_EXPANSION: dict[str, list[str]] = {
    "猪肉": ["猪", "排骨", "五花肉", "里脊", "猪蹄", "肉末", "肉馅", "腊肉", "火腿",
            "培根", "咸肉", "叉烧", "肉丸", "猪肝", "猪皮", "梅头肉"],
    "牛肉": ["牛", "牛腩", "牛排", "肥牛", "牛柳", "牛尾", "牛筋", "牛蹄筋"],
    "羊肉": ["羊", "羊排", "羊肉卷"],
    "鸡肉": ["鸡", "鸡胸", "鸡翅", "鸡腿", "鸡爪", "整鸡", "乌鸡", "鸡肉"],
    "鸭肉": ["鸭", "鸭血", "鸭肉"],
    "海鲜": ALLERGEN_ALIASES["海鲜"],
    "虾": ALLERGEN_ALIASES["虾"],
    "蟹": ALLERGEN_ALIASES["蟹"],
    "鱼": ALLERGEN_ALIASES["鱼"],
    "牛奶": ALLERGEN_ALIASES["牛奶"],
    "鸡蛋": ["蛋"],
    "豆制品": ALLERGEN_ALIASES["豆制品"],
    "豆类": ALLERGEN_ALIASES["豆类"],
    "坚果": ALLERGEN_ALIASES["坚果"],
    "花生": ALLERGEN_ALIASES["花生"],
    "辣": ["辣", "豆瓣", "泡椒", "剁椒", "小米辣", "辣椒粉", "辣椒油", "藤椒", "花椒"],
}


@dataclass
class ProfileConstraints:
    """单人约束：来源档案 + 会话追加约束。"""
    profile: dict = field(default_factory=dict)
    allergens: list[str] = field(default_factory=list)
    taboo_keys: list[str] = field(default_factory=list)      # 疾病/人群忌口键
    taste_pref: str | None = None                            # 规范口味
    health_needs: list[str] = field(default_factory=list)
    extra_banned: list[str] = field(default_factory=list)    # 会话中用户明确忌口
    # 运行时缓存
    _banned_re: re.Pattern | None = None
    _allergen_re: re.Pattern | None = None

    # ---------- 构建 ----------
    @classmethod
    def from_profile(cls, profile: dict | None) -> "ProfileConstraints":
        profile = profile or {}
        allergens = [a for a in profile.get("过敏食材", []) or [] if a]
        taboo_keys = []
        for item in profile.get("特殊人群", []) or []:
            key = CROWD_TO_TABOO.get(str(item).strip())
            if key and key not in taboo_keys:
                taboo_keys.append(key)
        taste = profile.get("口味偏好")
        return cls(
            profile=profile,
            allergens=allergens,
            taboo_keys=taboo_keys,
            taste_pref=TASTE_PREF_MAP.get(taste, None),
            health_needs=list(profile.get("健康需求", []) or []),
        )

    def add_banned(self, keywords: list[str]):
        for k in keywords:
            if k and k not in self.extra_banned:
                self.extra_banned.append(k)
        self._banned_re = None

    def remove_banned(self, keyword: str):
        """解除会话追加忌口（仅 extra_banned）。

        过敏原/疾病忌口来自档案，属于健康安全约束，不因对话"想吃"而解除
        （赛题零违反按档案口径判定）。口味类会话忌口以最新对话为准。
        """
        if keyword in self.extra_banned:
            self.extra_banned.remove(keyword)
            self._banned_re = None

    # ---------- 违禁词表 ----------
    def allergen_keywords(self) -> list[tuple[str, str]]:
        """[(过敏原, 别名), ...]"""
        pairs = []
        for a in self.allergens:
            aliases = ALLERGEN_ALIASES.get(a, [a])
            pairs += [(a, al) for al in aliases]
        return pairs

    def taboo_keywords(self) -> list[tuple[str, str]]:
        pairs = []
        for k in self.taboo_keys:
            aliases = DISEASE_TABOOS.get(k, [])
            pairs += [(k, al) for al in aliases]
        return pairs

    def _extra_pairs(self) -> list[tuple[str, str]]:
        pairs = [("会话忌口", k) for k in self.extra_banned]
        # 同类食材扩展（不吃猪肉 → 同时拦住排骨/五花肉等）
        for k in self.extra_banned:
            for alias in SESSION_BANNED_EXPANSION.get(k, []):
                pairs.append((f"会话忌口({k})", alias))
        return pairs

    def _compile(self, pairs) -> re.Pattern:
        words = _alias_normalize([w for _, w in pairs] + [f"{s}" for s, _ in pairs])
        words = [re.escape(w) for w in words]
        return re.compile("|".join(words)) if words else re.compile(r"(?!x)x")  # 永不匹配

    @property
    def banned_pattern(self) -> re.Pattern:
        """全部违禁（过敏+忌口+会话忌口）合成一个正则，用于候选池过滤。"""
        if self._banned_re is None:
            pairs = self.allergen_keywords() + self.taboo_keywords() + self._extra_pairs()
            self._banned_re = self._compile(pairs)
        return self._banned_re

    # ---------- 校验 ----------
    def violations_of(self, rec: Recipe) -> list[dict]:
        """逐条列出违禁命中（含来源说明），供解释与自动替换。"""
        text = rec.name + "｜" + "、".join(i.name for i in rec.ingredients) + "｜" + rec.ingredient_text
        found = []
        for source, alias in self.allergen_keywords() + self.taboo_keywords() + self._extra_pairs():
            if alias and re.search(re.escape(alias), text):
                found.append({"source": source, "keyword": alias, "recipe": rec.name})
        # 去重（同 source+keyword）
        seen, uniq = set(), []
        for v in found:
            k = (v["source"], v["keyword"])
            if k not in seen:
                seen.add(k)
                uniq.append(v)
        return uniq

    def ok(self, rec: Recipe) -> bool:
        return not self.banned_pattern.search(
            rec.name + "｜" + "、".join(i.name for i in rec.ingredients) + "｜" + rec.ingredient_text
        )

    def summary(self) -> str:
        parts = []
        if self.allergens:
            parts.append("过敏：" + "、".join(self.allergens))
        if self.taboo_keys:
            parts.append("忌口：" + "、".join(self.taboo_keys))
        if self.extra_banned:
            parts.append("本轮忌口：" + "、".join(self.extra_banned))
        if self.taste_pref:
            parts.append("口味偏好：" + self.taste_pref)
        if self.health_needs:
            parts.append("健康需求：" + "、".join(self.health_needs))
        return "；".join(parts) if parts else "无特殊限制"


def merge_constraints(list_of: list[ProfileConstraints]) -> ProfileConstraints:
    """多人场景：合并为全体约束的交集（任何人的违禁都对整桌生效）。

    口味不做交集而是多值并存（"辣、清淡"）：不同人的口味偏好都应被照顾，
    由选菜环节做整桌分配（辣菜与清淡菜都安排）。
    """
    merged = ProfileConstraints(profile={})
    allergens, taboos, needs, banned, tastes = set(), set(), [], set(), []
    for c in list_of:
        allergens.update(c.allergens)
        taboos.update(c.taboo_keys)
        banned.update(c.extra_banned)
        needs += [n for n in c.health_needs if n not in needs]
        for t in (c.taste_pref or "").split("、"):
            if t and t not in tastes:
                tastes.append(t)
    merged.allergens = sorted(allergens)
    merged.taboo_keys = sorted(taboos)
    merged.extra_banned = sorted(banned)
    merged.health_needs = needs[:6]
    merged.taste_pref = "、".join(tastes[:3]) or None
    return merged
