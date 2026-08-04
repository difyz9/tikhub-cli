"""TikHub SDK — 声明式端点注册表 + SQLite 缓存 + HTTP 客户端.

用法:
    from tikhub_cli.sdk import TikHubClient

    with TikHubClient() as client:
        data = client.call("fetch_video_search", keyword="猫咪")
        print(len(data["data"]), "个结果")
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".tikhub" / "cache.db"
API_BASE = "https://api.tikhub.io"
DEFAULT_TTL = 1800

# ═══════════════════════════════════════════════════════════
# 端点注册表 — 覆盖 5 大平台 24 个端点
# ═══════════════════════════════════════════════════════════

ENDPOINTS: dict[str, dict] = {
    # ── Threads ──
    "threads_post": {
        "method": "GET", "path": "/api/v1/threads/web/fetch_post_detail_v2",
        "ttl": 3600, "desc": "Threads 帖子详情",
    },
    "threads_comments": {
        "method": "GET", "path": "/api/v1/threads/web/fetch_post_comments",
        "ttl": 600, "desc": "Threads 评论",
    },
    # ── 抖音 搜索 ──
    "dy_video_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_video_search_v1",
        "ttl": 300, "desc": "抖音视频搜索",
    },
    "dy_general_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_general_search_v1",
        "ttl": 300, "desc": "抖音综合搜索",
    },
    "dy_user_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_user_search_v2",
        "ttl": 300, "desc": "抖音用户搜索",
    },
    "dy_music_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_music_search",
        "ttl": 300, "desc": "抖音音乐搜索",
    },
    "dy_live_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_live_search_v1",
        "ttl": 300, "desc": "抖音直播搜索",
    },
    "dy_multi_search": {
        "method": "POST", "path": "/api/v1/douyin/search/fetch_multi_search",
        "ttl": 300, "desc": "抖音多重搜索",
    },
    # ── 抖音 内容 ──
    "dy_share_url": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_one_video_by_share_url",
        "ttl": 600, "desc": "通过分享链接获取视频详情",
    },
    "dy_user_posts": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_user_post_videos",
        "ttl": 300, "desc": "用户作品列表",
    },
    "dy_user_likes": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_user_like_videos",
        "ttl": 300, "desc": "用户喜欢作品",
    },
    "dy_user_collects": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_user_collects",
        "ttl": 600, "desc": "用户收藏夹",
    },
    "dy_user_profile": {
        "method": "GET", "path": "/api/v1/douyin/web/handler_user_profile",
        "ttl": 600, "desc": "用户档案",
    },
    "dy_comments": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_video_comments",
        "ttl": 120, "desc": "视频评论",
    },
    "dy_comment_replies": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_video_comment_replies",
        "ttl": 120, "desc": "评论回复",
    },
    "dy_hot_search": {
        "method": "GET", "path": "/api/v1/douyin/web/fetch_hot_search_result",
        "ttl": 3600, "desc": "抖音热搜榜",
    },
    "dy_mix_detail": {
        "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_video_mix_detail",
        "ttl": 600, "desc": "合集详情",
    },
    "dy_mix_posts": {
        "method": "GET", "path": "/api/v1/douyin/app/v3/fetch_video_mix_post_list",
        "ttl": 300, "desc": "合集作品列表",
    },
    "dy_topic_videos": {
        "method": "GET", "path": "/api/v1/douyin/creator/fetch_creator_material_center_related",
        "ttl": 1800, "desc": "话题/热点相关视频",
    },
    "dy_mission_tasks": {
        "method": "GET", "path": "/api/v1/douyin/creator/fetch_mission_task_list",
        "ttl": 3600, "desc": "商单任务列表",
    },
    # ── YouTube ──
    "yt_captions": {
        "method": "GET", "path": "/api/v1/youtube/web_v2/get_video_captions",
        "ttl": 86400, "desc": "YouTube 字幕",
    },
    "yt_post_detail": {
        "method": "GET", "path": "/api/v1/youtube/web_v2/get_post_detail",
        "ttl": 3600, "desc": "YouTube 帖子详情",
    },
    # ── Zhihu ──
    "zhihu_articles": {
        "method": "GET", "path": "/api/v1/zhihu/web/fetch_user_articles",
        "ttl": 1800, "desc": "知乎用户文章",
    },
    # ── Reddit ──
    "reddit_subreddits": {
        "method": "GET", "path": "/api/v1/reddit/app/fetch_user_active_subreddits",
        "ttl": 3600, "desc": "Reddit 用户活跃社区",
    },
}


# ═══════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════

class TikHubError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ═══════════════════════════════════════════════════════════
# 缓存
# ═══════════════════════════════════════════════════════════

class Cache:
    """SQLite 缓存 — 零外部依赖."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key      TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    params   TEXT NOT NULL,
                    data     TEXT NOT NULL,
                    created  REAL NOT NULL,
                    expires  REAL NOT NULL,
                    hits     INTEGER DEFAULT 0
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_endpoint ON cache(endpoint)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires)")
            db.commit()

    @staticmethod
    def _key(endpoint: str, params: dict | None = None) -> str:
        raw = endpoint + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, cache_key: str) -> dict | None:
        now = time.time()
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT data, expires FROM cache WHERE key = ?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            data_json, expires = row
            if now > expires:
                db.execute("DELETE FROM cache WHERE key = ?", (cache_key,))
                db.commit()
                return None
            db.execute("UPDATE cache SET hits = hits + 1 WHERE key = ?", (cache_key,))
            db.commit()
            return json.loads(data_json)

    def set(self, cache_key: str, endpoint: str, params: dict, data: dict, ttl: int) -> None:
        now = time.time()
        with sqlite3.connect(self.path) as db:
            db.execute(
                """INSERT OR REPLACE INTO cache
                   (key, endpoint, params, data, created, expires, hits)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (cache_key, endpoint, json.dumps(params, ensure_ascii=False),
                 json.dumps(data, ensure_ascii=False), now, now + ttl),
            )
            db.commit()

    def stats(self) -> dict:
        with sqlite3.connect(self.path) as db:
            total = db.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            valid = db.execute(
                "SELECT COUNT(*) FROM cache WHERE expires > ?", (time.time(),)
            ).fetchone()[0]
            return {"total": total, "valid": valid, "expired": total - valid}

    def clear(self, endpoint: str | None = None) -> int:
        with sqlite3.connect(self.path) as db:
            if endpoint:
                cur = db.execute("DELETE FROM cache WHERE endpoint = ?", (endpoint,))
            else:
                cur = db.execute("DELETE FROM cache")
            db.commit()
            return cur.rowcount


# ═══════════════════════════════════════════════════════════
# 客户端
# ═══════════════════════════════════════════════════════════

class TikHubClient:
    """TikHub API 客户端."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = API_BASE,
        db_path: str | Path = DB_PATH,
        default_ttl: int = DEFAULT_TTL,
    ) -> None:
        self.api_key = api_key or os.getenv("TIKHUB_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.default_ttl = default_ttl
        self.cache = Cache(db_path)
        self._http: httpx.Client | None = None

    # ── HTTP ──

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=30,
            )
        return self._http

    # ── 核心 ──

    def call(
        self,
        endpoint: str,
        use_cache: bool = True,
        force_refresh: bool = False,
        cache_ttl: int | None = None,
        **params: Any,
    ) -> dict:
        """调用 TikHub API.

        Args:
            endpoint: 端点名（见 ENDPOINTS）
            use_cache: 是否读缓存
            force_refresh: 强制跳过缓存
            cache_ttl: 覆盖默认 TTL
            **params: 端点参数（keyword=, url=, sec_user_id= 等）

        Returns:
            API 响应 data 字段
        """
        if endpoint not in ENDPOINTS:
            names = ", ".join(ENDPOINTS)
            raise ValueError(f"未知端点: {endpoint}\n可用: {names}")

        cfg = ENDPOINTS[endpoint]
        ttl = cache_ttl or cfg["ttl"]
        cache_key = Cache._key(endpoint, params)

        # 缓存
        if use_cache and not force_refresh:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit

        # 真实请求
        method = cfg["method"].upper()
        if method == "GET":
            resp = self.http.get(cfg["path"], params=params)
        else:
            resp = self.http.request(method, cfg["path"], json=params)

        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", 0)
        if code != 200:
            raise TikHubError(code, body.get("message", "未知错误"))

        data = body.get("data", {})

        # 写缓存
        if use_cache:
            self.cache.set(cache_key, endpoint, params, data, ttl)

        return data

    # ── 辅助 ──

    def list_endpoints(self, platform: str | None = None) -> list[dict]:
        """列出端点，可按平台过滤."""
        result = []
        for name, cfg in ENDPOINTS.items():
            if platform:
                prefix = platform.lower()
                if not (name.startswith(prefix) or name.startswith(f"dy_") and prefix == "douyin"):
                    continue
            result.append({"name": name, "desc": cfg["desc"], "ttl": cfg["ttl"],
                           "method": cfg["method"], "path": cfg["path"]})
        return result

    def stats(self) -> dict:
        return self.cache.stats()

    def clear(self, endpoint: str | None = None) -> int:
        return self.cache.clear(endpoint)

    def close(self) -> None:
        if self._http:
            self._http.close()
            self._http = None

    def __enter__(self) -> TikHubClient:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()
