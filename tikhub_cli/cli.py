"""TikHub CLI — 入口.

用法:
    tikhub search 猫咪 --limit 3
    tikhub download https://... -o ./out.mp4
    tikhub extract ./video.mp4 -n 5
    tikhub link "https://v.douyin.com/..."
    tikhub classify -f results.json
    tikhub report search 猫咪 -f results.json
    tikhub cache stats
    tikhub endpoints --platform douyin
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn

from tikhub_cli.sdk import TikHubClient, ENDPOINTS, TikHubError
from tikhub_cli.downloader import download as do_download, Result as DLResult

console = Console()


def _client(require_key: bool = True) -> TikHubClient:
    key = os.getenv("TIKHUB_API_KEY", "")
    if require_key and not key:
        raise click.UsageError("请设置 TIKHUB_API_KEY 环境变量")
    return TikHubClient(api_key=key)


# ═══════════════════════════════════════════════════
# 主命令
# ═══════════════════════════════════════════════════

@click.group()
@click.version_option(version="0.1.0", prog_name="tikhub")
def main() -> None:
    """TikHub CLI — 抖音/Threads/YouTube/Zhihu/Reddit 数据工具."""


# ═══════════════════════════════════════════════════
# 子命令: search
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("keyword")
@click.option("--endpoint", "-e", default="dy_video_search",
              help="端点名（默认 dy_video_search）")
@click.option("--limit", "-n", type=int, default=10, help="显示条数")
@click.option("--json", "as_json", is_flag=True, help="JSON 输出")
@click.option("--no-cache", is_flag=True, help="跳过缓存")
def search(keyword: str, endpoint: str, limit: int, as_json: bool, no_cache: bool) -> None:
    """搜索抖音/多平台内容."""
    client = _client()
    try:
        data = client.call(endpoint, keyword=keyword, use_cache=not no_cache)
    except TikHubError as e:
        console.print(f"[red]API 错误 [{e.code}]: {e.message}[/]")
        raise SystemExit(1)
    finally:
        client.close()

    if as_json:
        console.print_json(data=data)
        return

    items = _extract_items(data)
    if not items:
        console.print("[dim]无结果[/]")
        return

    table = Table(title=f"搜索: {keyword} ({endpoint})")
    table.add_column("#", style="dim")
    table.add_column("标题/描述", style="cyan")
    table.add_column("作者", style="green")
    table.add_column("点赞", justify="right")

    for i, item in enumerate(items[:limit], 1):
        info = _parse_item(item)
        table.add_row(str(i), info["title"], info["author"], str(info.get("likes", "")))

    console.print(table)


def _extract_items(data: dict) -> list[dict]:
    """从各种 API 返回格式中提取 item 列表."""
    if isinstance(data, list):
        return data
    if "data" in data:
        return data["data"] if isinstance(data["data"], list) else []
    # 抖音搜索返回格式: {data: {cursor: ..., data: [...]}}
    if isinstance(data.get("data"), dict):
        inner = data["data"].get("data")
        if isinstance(inner, list):
            return inner
    # aweme_list
    if "aweme_list" in data:
        return data["aweme_list"]
    return []


def _parse_item(item: dict) -> dict:
    """从 item 中提取通用字段."""
    aweme = item.get("aweme_info", item)
    return {
        "title": (aweme.get("desc") or aweme.get("title") or
                  item.get("title") or item.get("caption") or "-")[:60],
        "author": (aweme.get("author", {}).get("nickname") or
                   item.get("author") or "-")[:20],
        "likes": aweme.get("statistics", {}).get("digg_count",
                   item.get("digg_count", "")),
    }


# ═══════════════════════════════════════════════════
# 子命令: download
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("url")
@click.option("--output", "-o", default=None, help="输出路径")
@click.option("--quiet", "-q", is_flag=True, help="静默模式")
def download(url: str, output: str, quiet: bool) -> None:
    """下载视频文件（支持分段并发+断点续传）."""
    if not output:
        from urllib.parse import urlparse
        name = os.path.basename(urlparse(url).path) or "video.mp4"
        if "." not in name:
            name += ".mp4"
        output = str(Path.cwd() / name)

    if not quiet:
        console.print(f"[dim]下载 {url}[/]")
        console.print(f"[dim]→ {output}[/]")

    async def _run() -> DLResult:
        if quiet:
            return await do_download(url, output)
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
        ) as prog:
            tid = prog.add_task("下载中...", total=100)

            async def cb(pct: float) -> None:
                prog.update(tid, completed=pct)

            return await do_download(url, output, on_progress=cb)

    result = asyncio.run(_run())

    if result.success:
        mb = result.size / 1024 / 1024
        console.print(
            f"[green]✓ 完成[/] {result.path} "
            f"({mb:.1f}MB, {result.duration:.1f}s)"
        )
    else:
        console.print(f"[red]✗ 失败[/] {result.error}")


# ═══════════════════════════════════════════════════
# 子命令: extract
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("video")
@click.option("--count", "-n", type=int, default=5, help="帧数")
@click.option("--output", "-o", default=".", help="输出目录")
@click.option("--timestamps", "-t", multiple=True, type=float, help="指定时间点（秒）")
def extract(video: str, count: int, output: str, timestamps: tuple[float, ...]) -> None:
    """从视频中提取关键帧."""
    from tikhub_cli.extractor import extract as do_extract

    ts = list(timestamps) if timestamps else None
    try:
        frames = asyncio.run(do_extract(video, output, count, ts))
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]错误: {e}[/]")
        raise SystemExit(1)

    if not frames:
        console.print("[dim]未提取到帧[/]")
        return

    table = Table(title=f"帧提取: {os.path.basename(video)}")
    table.add_column("#", style="dim")
    table.add_column("时间", justify="right")
    table.add_column("尺寸", justify="center")
    table.add_column("大小", justify="right")
    table.add_column("路径", style="cyan")

    for i, f in enumerate(frames, 1):
        table.add_row(
            str(i), f"{f.timestamp:.1f}s",
            f"{f.width}x{f.height}" if f.width else "-",
            f"{f.size / 1024:.0f}KB", f.path,
        )

    console.print(table)


# ═══════════════════════════════════════════════════
# 子命令: cache
# ═══════════════════════════════════════════════════

@main.group()
def cache() -> None:
    """管理 SQLite 缓存."""


@cache.command()
def stats() -> None:
    """缓存统计."""
    client = _client(require_key=False)
    try:
        s = client.stats()
    finally:
        client.close()
    console.print(f"总条目: {s['total']}  |  有效: {s['valid']}  |  过期: {s['expired']}")


@cache.command()
@click.option("--endpoint", "-e", default=None, help="清除指定端点缓存")
def clear(endpoint: str | None) -> None:
    """清除缓存."""
    client = _client(require_key=False)
    try:
        n = client.clear(endpoint)
    finally:
        client.close()
    scope = endpoint or "全部"
    console.print(f"[green]✓ 已清除 {scope} 缓存 ({n} 条)[/]")


# ═══════════════════════════════════════════════════
# 子命令: endpoints
# ═══════════════════════════════════════════════════

@main.command()
@click.option("--platform", "-p", default=None,
              help="平台过滤: douyin/threads/youtube/zhihu/reddit")
def endpoints(platform: str | None) -> None:
    """列出所有可用端点."""
    table = Table(title="可用 API 端点")
    table.add_column("端点名", style="cyan")
    table.add_column("描述")
    table.add_column("方法", style="yellow")
    table.add_column("TTL", justify="right")
    table.add_column("路径", style="dim")

    for ep in sorted(ENDPOINTS.items(), key=lambda x: x[0]):
        name, cfg = ep
        if platform:
            pfx = {"douyin": "dy_", "threads": "threads_", "youtube": "yt_",
                   "zhihu": "zhihu_", "reddit": "reddit_"}.get(platform, "")
            if not name.startswith(pfx):
                continue
        table.add_row(name, cfg["desc"], cfg["method"],
                      f"{cfg['ttl']}s", cfg["path"])

    console.print(table)


# ═══════════════════════════════════════════════════
# 子命令: info
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("url")
@click.option("--no-cache", is_flag=True, help="跳过缓存")
def info(url: str, no_cache: bool) -> None:
    """通过分享链接获取视频详情."""
    client = _client()
    try:
        data = client.call("dy_share_url", share_url=url, use_cache=not no_cache)
        console.print_json(data=data)
    except TikHubError as e:
        console.print(f"[red]API 错误 [{e.code}]: {e.message}[/]")
        raise SystemExit(1)
    finally:
        client.close()


# ═══════════════════════════════════════════════════
# 子命令: link — 通用链接解析+下载 (yt-dlp)
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("url")
@click.option("--output", "-o", default=".", help="输出目录")
@click.option("--resolve-only", is_flag=True, help="只解析不下载")
@click.option("--no-merge", is_flag=True, help="不合并音视频流")
def link(url: str, output: str, resolve_only: bool, no_merge: bool) -> None:
    """用 yt-dlp 解析并下载任意公开链接（抖音/B站/YouTube/小红书等）."""
    try:
        from tikhub_cli.resolver import resolve, download_link, detect_platform
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)

    platform = detect_platform(url)
    console.print(f"[dim]平台: {platform}[/]")

    if resolve_only:
        try:
            info = resolve(url)
            console.print(f"标题: {info.get('title', '-')}")
            console.print(f"上传者: {info.get('uploader', '-')}")
            console.print(f"时长: {info.get('duration', 0):.0f}s")
            fmts = info.get("formats") or []
            console.print(f"可用格式: {len(fmts)} 个")
            for f in fmts[:5]:
                res = f.get("resolution") or f"{f.get('width','?')}x{f.get('height','?')}"
                fs = f.get("filesize", 0)
                console.print(
                    f"  [dim]{f.get('format_id', '?')}[/] "
                    f"{f.get('ext', '?')} {res} "
                    f"[dim]{fs // 1024 if fs else '?'}KB[/]"
                )
            if len(fmts) > 5:
                console.print(f"  [dim]... 还有 {len(fmts) - 5} 个格式[/]")
        except Exception as e:
            console.print(f"[red]解析失败: {e}[/]")
            raise SystemExit(1)
        return

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        SpinnerColumn(),
    ) as prog:
        tid = prog.add_task("解析并下载中...", total=None)
        try:
            result = download_link(url, output, merge=not no_merge)
        except Exception as e:
            prog.remove_task(tid)
            console.print(f"[red]错误: {e}[/]")
            raise SystemExit(1)
        prog.remove_task(tid)

    if result["success"]:
        total_mb = result.get("total_size", 0) / 1024 / 1024
        console.print(f"[green]✓ 下载完成[/] {result['title']}")
        for f in result.get("files", []):
            console.print(f"  → {f}")
        console.print(f"[dim]平台: {result['platform']} | 提取器: {result.get('extractor', '-')} | 总大小: {total_mb:.1f}MB[/]")
    else:
        console.print(f"[red]✗ 下载失败[/] {result.get('error', '未知错误')}")


# ═══════════════════════════════════════════════════
# 子命令: classify — 内容分类
# ═══════════════════════════════════════════════════

@main.command()
@click.option("--file", "-f", default=None, help="JSON 文件路径（每行一个对象或整个数组）")
@click.option("--stdin", "from_stdin", is_flag=True, help="从标准输入读取 JSON")
def classify(file: str | None, from_stdin: bool) -> None:
    """对内容进行 17 类赛道分类."""
    from tikhub_cli.classifier import classify_batch

    if from_stdin:
        raw = sys.stdin.read()
    elif file:
        raw = Path(file).read_text(encoding="utf-8")
    else:
        console.print("[red]请指定 --file 或 --stdin[/]")
        raise SystemExit(1)

    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
    except json.JSONDecodeError:
        # 尝试每行一个 JSON
        items = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not items:
        console.print("[dim]无有效数据[/]")
        return

    # 分类并显示
    table = Table(title=f"内容分类 ({len(items)} 条)")
    table.add_column("分类", style="cyan")
    table.add_column("数量", justify="right")
    table.add_column("占比", justify="right")

    stats = classify_batch(items)
    for cat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        pct = cnt / len(items) * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        table.add_row(cat, str(cnt), f"{bar} {pct:.1f}%")

    console.print(table)


# ═══════════════════════════════════════════════════
# 子命令: report — 生成 HTML 报告
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("title")
@click.option("--file", "-f", default=None, help="JSON 数据文件")
@click.option("--output", "-o", default="report.html", help="输出路径")
def report(title: str, file: str | None, output: str) -> None:
    """从 JSON 数据生成离线 HTML 报告（自动分类）."""
    from tikhub_cli.report import build_search_report

    if not file:
        console.print("[red]请指定 --file 数据文件[/]")
        raise SystemExit(1)

    raw = Path(file).read_text(encoding="utf-8")
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
    except json.JSONDecodeError:
        items = []
        for line in raw.strip().splitlines():
            if line.strip():
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not items:
        console.print("[dim]无有效数据[/]")
        return

    path = build_search_report(title, items, output)
    console.print(f"[green]✓ 报告已生成[/] {path}")
    console.print(f"  [dim]用浏览器打开即可查看[/]")


# ═══════════════════════════════════════════════════
# 子命令: login — 抖音扫码登录
# ═══════════════════════════════════════════════════

@main.command()
@click.option("--timeout", type=int, default=180, help="等待超时秒数")
def login(timeout: int) -> None:
    """打开浏览器扫码登录抖音，保存登录态."""
    try:
        from tikhub_cli.collector import login as do_login
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)

    try:
        result = do_login(timeout=timeout)
        console.print(f"[green]✓ 登录成功[/]")
        console.print(f"  用户: {result.get('sec_user_id', '-')}")
        console.print(f"  状态文件: {result['auth_path']}")
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/]")
        raise SystemExit(1)


# ═══════════════════════════════════════════════════
# 子命令: fetch — 采集点赞/收藏
# ═══════════════════════════════════════════════════

@main.command()
@click.argument("source", type=click.Choice(["likes", "favorites", "all"]), default="all")
@click.option("--pages", "-p", type=int, default=3, help="每类采集页数")
@click.option("--download/--no-download", default=True, help="采集后自动下载（默认下载）")
@click.option("--limit", "-n", type=int, default=0, help="最多下载视频数（0=全部）")
@click.option("--output", "-o", default=None, help="下载目录")
@click.option("--headless", is_flag=True, help="无头模式（可能触发验证）")
@click.option("--json", "as_json", is_flag=True, help="JSON 输出采集结果")
def fetch(
    source: str, pages: int, download: bool, limit: int,
    output: str | None, headless: bool, as_json: bool,
) -> None:
    """采集自己的抖音点赞/收藏，可选自动下载视频."""
    try:
        from tikhub_cli.collector import fetch as do_fetch, download_collected
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)

    console.print(f"[dim]采集 {source}（最多 {pages} 页）...[/]")
    try:
        result = do_fetch(source=source, pages=pages, headless=headless)
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/]")
        raise SystemExit(1)

    if as_json:
        # 去掉 items 字段，太大
        clean = {k: {kk: vv for kk, vv in v.items() if kk != "items"} for k, v in result.items() if k != "total"}
        clean["total"] = result["total"]
        console.print_json(data=clean)
        return

    for t in ["favorites", "likes"]:
        if t in result:
            r = result[t]
            console.print(f"  {t}: {r['pages']} 页, {r['videos']} 个视频")

    total = result.get("total", 0)
    if total == 0:
        console.print("[dim]无内容[/]")
        return

    console.print(f"[green]共采集 {total} 个视频[/]")

    if not download:
        return

    # 合并所有视频
    all_items = []
    for t in ["favorites", "likes"]:
        all_items.extend(result.get(t, {}).get("items", []))

    if not all_items:
        return

    console.print(f"[dim]开始下载 {min(len(all_items), limit) if limit else len(all_items)} 个视频...[/]")
    dl_result = download_collected(all_items, out_dir=output, limit=limit)

    table = Table(title="下载结果")
    table.add_column("状态", justify="center")
    table.add_column("数量", justify="right")
    table.add_row("[green]✓ 成功[/]", str(dl_result["success"]))
    if dl_result["failed"]:
        table.add_row("[red]✗ 失败[/]", str(dl_result["failed"]))
    table.add_row("合计", str(dl_result["total"]))
    console.print(table)


# ═══════════════════════════════════════════════════
# 子命令: status — 登录状态
# ═══════════════════════════════════════════════════

@main.command()
def status() -> None:
    """查看抖音登录状态."""
    try:
        from tikhub_cli.collector import status as do_status
    except RuntimeError:
        console.print("[dim]未安装 douyin 模块。pip install 'tikhub-cli[douyin]'[/]")
        return

    s = do_status()
    if s["logged_in"]:
        console.print(f"[green]✓ 已登录[/]")
    else:
        console.print(f"[yellow]○ 未登录[/]")

    table = Table(show_header=False)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("用户 ID", s.get("sec_user_id", "-"))
    table.add_row("保存时间", s.get("saved_at", "-"))
    table.add_row("Cookie 数", str(s.get("cookie_count", 0)))
    table.add_row("状态文件", s.get("auth_path", "-"))
    table.add_row("浏览器目录", s.get("profile_dir", "-"))
    console.print(table)
