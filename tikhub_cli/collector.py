"""抖音个人数据采集 — Playwright 浏览器登录 + API 拦截.

用法:
    from tikhub_cli.collector import login, fetch

    login()                                    # 扫码登录
    result = fetch("favorites", pages=5)       # 采集收藏
    result = fetch("likes", pages=5)           # 采集点赞
    result = fetch("all", pages=3, download=True)  # 全部采集+下载

存储:
    ~/.tikhub/auth.json     — 浏览器登录态
    ~/.tikhub/cache.db      — 视频数据缓存
    ~/.tikhub/downloads/    — 下载的视频
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ═══════════════════════════════════════
# 路径
# ═══════════════════════════════════════

DATA_DIR = Path.home() / ".tikhub"
AUTH_PATH = DATA_DIR / "auth.json"
DOWNLOADS_DIR = DATA_DIR / "downloads"
BROWSER_PROFILE = DATA_DIR / "browser_profile"

# 抖音 API 端点
HOME_URL = "https://www.douyin.com/"
FAV_TAB_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"

RESPONSE_SOURCES = {
    "/aweme/v1/web/aweme/listcollection/": "favorites",
    "/aweme/v1/web/aweme/favorite/": "likes",
}


# ═══════════════════════════════════════
# Auth 管理
# ═══════════════════════════════════════

def load_auth() -> dict:
    if AUTH_PATH.exists():
        try:
            return json.loads(AUTH_PATH.read_text())
        except (OSError, ValueError):
            pass
    return {}


def save_auth(state: dict, sec_uid: str = "") -> Path:
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["sec_user_id"] = sec_uid or _extract_cookie(state, "sec_user_id")
    state["saved_at"] = datetime.now().isoformat(timespec="seconds")
    AUTH_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return AUTH_PATH


def _extract_cookie(auth: dict, name: str) -> str:
    for c in auth.get("cookies") or []:
        if c.get("name") == name:
            return str(c.get("value") or "")
    return ""


def has_session(auth: dict | None = None) -> bool:
    a = auth or load_auth()
    return bool(_extract_cookie(a, "sessionid") or _extract_cookie(a, "sessionid_ss"))


def _cookie_dict() -> dict[str, str]:
    return {
        c["name"]: c["value"]
        for c in (load_auth().get("cookies") or [])
        if c.get("name") and c.get("value")
    }


# ═══════════════════════════════════════
# 浏览器
# ═══════════════════════════════════════

def _launch_browser(playwright: Any, headless: bool = False) -> Any:
    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("DOUKU_BROWSER_CHANNEL", "msedge")
    opts = {
        "user_data_dir": str(BROWSER_PROFILE),
        "headless": headless,
        "locale": "zh-CN",
        "accept_downloads": False,
    }
    try:
        return playwright.chromium.launch_persistent_context(channel=channel, **opts)
    except Exception:
        return playwright.chromium.launch_persistent_context(**opts)


def _ensure_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise RuntimeError("需要 playwright。安装: pip install 'tikhub-cli[douyin]'")


# ═══════════════════════════════════════
# 登录
# ═══════════════════════════════════════

def login(timeout: int = 180) -> dict:
    """打开浏览器让用户扫码登录，保存登录态."""
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _launch_browser(pw)
        # 尝试导入旧 state
        existing = load_auth()
        if has_session(existing):
            cookies = existing.get("cookies") or []
            if cookies:
                ctx.add_cookies(cookies)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        print("请在打开的浏览器窗口中扫码登录抖音。")
        print("检测到登录成功后会自动保存...")

        deadline = time.time() + timeout
        while time.time() < deadline:
            state = ctx.storage_state()
            if has_session(state):
                sec_uid = _extract_cookie(state, "sec_user_id")
                save_auth(state, sec_uid)
                ctx.close()
                return {
                    "success": True,
                    "auth_path": str(AUTH_PATH),
                    "profile_dir": str(BROWSER_PROFILE),
                    "sec_user_id": sec_uid,
                }
            page.wait_for_timeout(1_000)

        ctx.close()
    raise RuntimeError(f"等待登录超时（{timeout}秒），请重新运行 login")


# ═══════════════════════════════════════
# 数据采集
# ═══════════════════════════════════════

def fetch(
    source: str = "all",
    pages: int = 3,
    headless: bool = False,
) -> dict:
    """采集个人点赞/收藏数据.

    Args:
        source: "favorites" | "likes" | "all"
        pages: 每种最多采集页数
        headless: 是否无头模式

    Returns:
        {"favorites": {"pages": N, "videos": N}, "likes": ...}
    """
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    targets = ["favorites", "likes"] if source == "all" else [source]
    results: dict[str, dict] = {t: {"pages": 0, "videos": 0} for t in targets}
    all_videos: list[dict] = []
    sec_uid = ""

    with sync_playwright() as pw:
        ctx = _launch_browser(pw, headless=headless)

        # 恢复登录态
        auth = load_auth()
        if has_session(auth):
            cookies = auth.get("cookies") or []
            if cookies:
                ctx.add_cookies(cookies)

        state = ctx.storage_state()
        if not has_session(state):
            ctx.close()
            raise RuntimeError("未登录。请先运行: tikhub login")

        sec_uid = _extract_cookie(state, "sec_user_id") or auth.get("sec_user_id", "")

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 拦截 API 响应
        captured: dict[str, list[dict]] = {"favorites": [], "likes": []}

        def handle_response(resp: Any) -> None:
            path = urlparse(resp.url).path
            source_name = next(
                (v for suffix, v in RESPONSE_SOURCES.items() if path.endswith(suffix)),
                None,
            )
            if source_name is None or source_name not in targets:
                return
            if results[source_name]["pages"] >= pages:
                return
            try:
                data = resp.json()
                if resp.status != 200 or data.get("status_code") not in (None, 0):
                    return
                items = (data.get("aweme_list") or data.get("aweme_list_data") or [])
                for item in items:
                    captured[source_name].append(item)
                results[source_name]["pages"] += 1
                results[source_name]["videos"] += len(items)
            except Exception:
                pass

        page.on("response", handle_response)

        # 打开收藏页
        page.goto(FAV_TAB_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(10_000)

        # 如果喜欢 tab 没触发，手动切
        if "likes" in targets and results["likes"]["pages"] == 0:
            if sec_uid:
                page.goto(
                    f"https://www.douyin.com/user/{sec_uid}?showTab=like",
                    wait_until="domcontentloaded", timeout=60_000,
                )
                page.wait_for_timeout(10_000)
            else:
                likes_btn = page.get_by_text("喜欢", exact=True)
                if likes_btn.count() == 1:
                    likes_btn.click()
                    page.wait_for_timeout(6_000)

        # 滚动触发分页
        for _ in range(pages + 2):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3_000)
            if all(results[t]["pages"] >= pages for t in targets):
                break

        page.remove_listener("response", handle_response)

        # 保存更新后的登录态
        save_auth(ctx.storage_state(), sec_uid)
        ctx.close()

    # 汇总
    for t in targets:
        results[t]["videos"] = len(captured[t])
        results[t]["items"] = captured[t]

    total = sum(results[t]["videos"] for t in targets)
    results["total"] = total
    return results


# ═══════════════════════════════════════
# 下载采集的视频
# ═══════════════════════════════════════

def download_collected(
    items: list[dict],
    out_dir: str | None = None,
    limit: int = 0,
) -> dict:
    """下载采集到的视频.

    Args:
        items: fetch() 返回的视频列表
        out_dir: 输出目录（默认 ~/.tikhub/downloads/）
        limit: 最多下载数（0 = 全部）

    Returns:
        {"success": N, "failed": N, "files": [...]}
    """
    import requests

    out = Path(out_dir or DOWNLOADS_DIR).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    cookies = _cookie_dict()
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
    })

    success = failed = 0
    files: list[str] = []
    batch = items[:limit] if limit > 0 else items

    for i, item in enumerate(batch, 1):
        aweme = item.get("aweme_info", item)
        aweme_id = aweme.get("aweme_id", str(i))
        desc = (aweme.get("desc") or "")[:40]
        author = (aweme.get("author", {}).get("nickname") or "unknown")[:16]
        safe_name = _safe_filename(f"{author}_{desc}")

        # 提取视频 URL
        video = aweme.get("video") or {}
        play = video.get("play_addr") or video.get("play_addr_h264") or {}
        urls = play.get("url_list") or []
        if not urls:
            failed += 1
            continue

        target = out / f"{safe_name}.mp4"

        # 跳过已存在
        if target.exists() and target.stat().st_size > 1024:
            success += 1
            files.append(str(target))
            print(f"[{i}/{len(batch)}] ✓ (已存在) {safe_name}")
            continue

        # 下载
        print(f"[{i}/{len(batch)}] ↓ {safe_name}")
        ok, err = _download_file(session, urls[0], target)
        if ok:
            success += 1
            files.append(str(target))
        else:
            failed += 1
            print(f"  ✗ {err}")

    return {"success": success, "failed": failed, "total": len(batch), "files": files}


# ═══════════════════════════════════════
# 底层下载
# ═══════════════════════════════════════

def _download_file(
    session: Any,
    url: str,
    target: Path,
    min_size: int = 1024,
) -> tuple[bool, str]:
    try:
        resp = session.get(url, stream=True, timeout=(15, 120))
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "").lower()
        if "text/html" in ct or "application/json" in ct:
            return False, "资源地址已过期"

        with open(target, "wb") as f:
            for chunk in resp.iter_content(256 * 1024):
                if chunk:
                    f.write(chunk)

        if target.stat().st_size < min_size:
            target.unlink()
            return False, "下载内容过小"

        return True, ""
    except Exception as e:
        if target.exists():
            target.unlink()
        return False, str(e)


# ═══════════════════════════════════════
# 辅助
# ═══════════════════════════════════════

def _safe_filename(s: str, max_len: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(s or "video"))
    s = re.sub(r"[\s_]+", "_", s).strip(" ._")
    return s[:max_len].rstrip(" ._")


def status() -> dict:
    """检查登录状态."""
    auth = load_auth()
    return {
        "logged_in": has_session(auth),
        "auth_path": str(AUTH_PATH),
        "sec_user_id": auth.get("sec_user_id", ""),
        "saved_at": auth.get("saved_at", ""),
        "cookie_count": len(auth.get("cookies", [])),
        "profile_dir": str(BROWSER_PROFILE),
        "profile_exists": BROWSER_PROFILE.exists(),
    }
