"""HTML 数据报告生成 — 离线可打开.

用法:
    from tikhub_cli.report import build_report

    build_report(
        title="抖音搜索: 猫咪",
        items=[{"title": "...", "author": "...", "category": "萌宠", "likes": 123}],
        output="report.html",
    )
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def build_report(
    title: str,
    items: list[dict[str, Any]],
    output: str = "report.html",
    *,
    summary: dict[str, Any] | None = None,
) -> Path:
    """生成离线 HTML 报告.

    Args:
        title: 报告标题
        items: 内容列表，每条含 title/author/category/likes/url 等
        output: 输出路径
        summary: 顶部汇总卡片，如 {"总条数": 100, "分类数": 8}

    Returns:
        输出文件绝对路径
    """
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    cards = ""
    if summary:
        cards = "".join(
            f"<div class='card'><strong>{v}</strong><span>{k}</span></div>"
            for k, v in summary.items()
        )

    # 分类统计
    cat_stats: dict[str, int] = {}
    for item in items:
        cat = item.get("category", "未分类")
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
    cat_rows = [[c, str(n)] for c, n in sorted(cat_stats.items(), key=lambda x: -x[1])]

    # 表格
    headers = ["标题", "作者", "分类", "点赞", "链接"]
    rows = []
    for item in items:
        rows.append([
            (item.get("title") or item.get("desc") or "-")[:60],
            (item.get("author") or item.get("nickname") or "-")[:20],
            item.get("category", "未分类"),
            str(item.get("likes") or item.get("digg_count") or ""),
            f"<a href='{html.escape(item.get('url') or item.get('share_url') or '#')}' target='_blank'>🔗</a>",
        ])

    body = "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
        for row in rows
    )

    cat_body = "".join(
        f"<tr><td>{html.escape(str(r[0]))}</td><td>{r[1]}</td></tr>"
        for r in cat_rows
    )

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:14px/1.6 system-ui,-apple-system,sans-serif;margin:0;background:#f5f6f8;color:#1f2329}}
main{{max-width:1200px;margin:auto;padding:24px}}
h1{{margin:0 0 20px;font-size:22px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin:0 0 24px}}
.card{{background:white;border-radius:10px;padding:16px;box-shadow:0 1px 3px #0001}}
.card strong{{display:block;font-size:26px;color:#1f2329}}
.card span{{color:#8b95a1;font-size:13px}}
section{{background:white;border-radius:10px;padding:18px;margin:14px 0;overflow:auto}}
h2{{font-size:16px;margin:0 0 12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:8px 10px;border-bottom:1px solid #eee;text-align:left}}
th{{background:#fafafa;position:sticky;top:0;font-weight:600}}
tr:hover{{background:#f8f9fb}}
a{{color:#2563eb;text-decoration:none}}
.muted{{color:#8b95a1}}
</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<div class="cards">{cards}</div>
<section><h2>分类分布</h2>
<table><thead><tr><th>分类</th><th>数量</th></tr></thead><tbody>{cat_body}</tbody></table>
</section>
<section><h2>内容列表 ({len(rows)} 条)</h2>
<table><thead><tr>{"".join(f"<th>{h}</th>" for h in headers)}</tr></thead>
<tbody>{body}</tbody></table>
</section>
</main>
</body>
</html>"""

    path.write_text(html_doc, encoding="utf-8")
    return path


def build_search_report(
    keyword: str,
    items: list[dict[str, Any]],
    output: str = "report.html",
) -> Path:
    """快捷生成搜索报告（自动分类）."""
    from tikhub_cli.classifier import classify as do_classify, classify_batch

    # 给每条内容打分类标签
    for item in items:
        result = do_classify({"desc": item.get("title") or item.get("desc", ""),
                           "tags": item.get("tags", []), "hashtags": item.get("hashtags", [])})
        item["category"] = result["category"]

    stats = classify_batch(items)
    return build_report(
        title=f"TikHub 搜索报告: {keyword}",
        items=items,
        output=output,
        summary={"总条数": len(items), "分类数": len(stats)},
    )
