"""视频内容分类 — 17 类赛道自动标记.

Layer 1: 抖音官方 video_tags → 目标分类映射
Layer 2: 标题/文案关键词推断
回退: "其他"

用法:
    from tikhub_cli.classifier import classify, CATEGORIES

    result = classify({"desc": "今天做了一道拿手菜...", "tags": ["美食", "烹饪"]})
    # -> {"category": "美食", "confidence": 8.0, "source": "tag_map"}
"""

from __future__ import annotations

from typing import Any

# ═══════════════════════════════════
# 17 个分类
# ═══════════════════════════════════

CATEGORIES = [
    "颜值", "舞蹈", "二次元", "游戏", "美食", "知识", "剧情",
    "搞笑", "音乐", "萌宠", "时尚", "生活", "情感", "运动",
    "影视", "明星", "其他",
]

# Layer 1: 标签映射
TAG_MAP: dict[str, set[str]] = {
    "颜值": {"颜值", "美女", "帅哥", "自拍", "素颜"},
    "舞蹈": {"舞蹈", "街舞", "芭蕾", "韩舞", "手势舞", "舞动"},
    "二次元": {"二次元", "动漫", "cos", "cosplay", "动画", "番剧", "漫画"},
    "游戏": {"游戏", "电竞", "王者荣耀", "原神", "吃鸡", "主机游戏", "网游", "手游"},
    "美食": {"美食", "吃播", "烹饪", "探店", "深夜放毒", "烘培", "菜谱", "小吃"},
    "知识": {"知识", "科普", "涨知识", "教育", "学习", "历史", "科技", "财经", "心理学"},
    "剧情": {"剧情", "短剧", "演绎", "情景剧", "反转", "脑洞"},
    "搞笑": {"搞笑", "幽默", "段子", "沙雕", "整活", "笑死"},
    "音乐": {"音乐", "唱歌", "翻唱", "乐器", "弹唱", "说唱", "声乐"},
    "萌宠": {"萌宠", "狗", "猫", "宠物", "金毛", "哈士奇", "喵星人"},
    "时尚": {"时尚", "穿搭", "美妆", "护肤", "变装", "发型", "化妆"},
    "生活": {"生活", "日常", "volg", "Vlog", "记录", "家居", "旅行", "亲子", "三农"},
    "情感": {"情感", "恋爱", "婚姻", "文案", "扎心", "治愈", "鸡汤"},
    "运动": {"运动", "健身", "体育", "篮球", "足球", "跑步", "瑜伽", "极限运动"},
    "影视": {"影视", "电影", "电视剧", "剪辑", "解说", "影评", "综艺"},
    "明星": {"明星", "演员", "歌手", "偶像", "娱乐圈", "综艺"},
}

# Layer 2: 关键词规则
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("二次元", ["cos", "cosplay", "动漫", "原神", "二次元", "番剧推荐", "漫剪", "oc", "设子"]),
    ("游戏", ["游戏", "GTA", "steam", "通关", "攻略", "电竞", "吃鸡", "王者", "原神",
              "我的世界", "mc", "lol", "游戏日常", "游戏实况", "打游戏", "上分"]),
    ("美食", ["美食", "吃播", "探店", "烹饪", "菜谱", "做饭", "厨房", "烘焙", "甜品", "吃货", "小吃"]),
    ("知识", ["科普", "知识", "干货", "教程", "学习", "冷知识", "涨知识", "财经", "科技", "历史"]),
    ("搞笑", ["搞笑", "幽默", "段子", "整活", "神操作", "笑死", "哈哈哈哈"]),
    ("萌宠", ["狗", "猫", "宠物", "金毛", "哈士奇", "猫咪", "狗狗", "萌宠"]),
    ("时尚", ["穿搭", "美妆", "护肤", "化妆", "变装", "发型", "ootd", "口红"]),
    ("颜值", ["颜值", "美女", "帅哥", "自拍", "神仙颜值"]),
    ("舞蹈", ["舞蹈", "舞", "街舞", "爵士", "kpop", "编舞"]),
    ("音乐", ["唱歌", "翻唱", "音乐", "弹唱", "乐器", "钢琴", "吉他", "原创歌曲"]),
    ("运动", ["健身", "运动", "减脂", "增肌", "瑜伽", "跑步", "篮球", "足球", "体育"]),
    ("情感", ["情感", "恋爱", "分手", "文案", "扎心", "治愈", "前任", "异地恋"]),
    ("生活", ["日常", "vlog", "volg", "记录", "旅行", "亲子", "农村", "三农", "种植"]),
    ("影视", ["电影", "电视剧", "影评", "解说", "影视", "剪辑", "番剧", "国漫"]),
    ("剧情", ["短剧", "剧情", "反转", "悬疑", "虐心", "甜剧"]),
    ("明星", ["明星", "演员", "歌手", "偶像", "娱乐圈"]),
]


# ═══════════════════════════════════
# 分类函数
# ═══════════════════════════════════

def classify(item: dict[str, Any]) -> dict[str, Any]:
    """对单条内容分类.

    Args:
        item: 包含 desc/title, tags, hashtags 等字段

    Returns:
        {"category": str, "confidence": float, "source": str}
    """
    desc = (item.get("desc") or item.get("title") or "").lower()
    tags: set[str] = set()

    # 收集标签
    for field in ("tags", "video_tags", "hashtags", "desc_hashtags"):
        raw = item.get(field) or []
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",")]
        for t in raw:
            tag = (t.get("tag_name") or t.get("name") or t if isinstance(t, dict) else t)
            if tag:
                tags.add(str(tag).lower())

    # Layer 1: 标签映射
    for cat, keywords in TAG_MAP.items():
        if tags & keywords:
            return {"category": cat, "confidence": 8.0, "source": "tag_map"}

    # Layer 2: 关键词推断
    best, best_score = "其他", 0
    for cat, keywords in KEYWORD_RULES:
        score = sum(1 for kw in keywords if kw.lower() in desc)
        score += sum(2 for kw in keywords if kw.lower() in tags)
        if score > best_score:
            best_score, best = score, cat

    if best_score >= 2:
        return {"category": best, "confidence": min(best_score * 2, 8.0),
                "source": "keyword"}
    if best_score >= 1:
        return {"category": best, "confidence": 3.0, "source": "keyword_weak"}

    return {"category": "其他", "confidence": 1.0, "source": "fallback"}


def classify_batch(items: list[dict[str, Any]]) -> dict[str, int]:
    """批量分类，返回统计."""
    stats: dict[str, int] = {}
    for item in items:
        cat = classify(item)["category"]
        stats[cat] = stats.get(cat, 0) + 1
    return stats
