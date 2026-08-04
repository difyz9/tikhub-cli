"""抖音实时监控 — 定期检查新点赞/收藏并自动下载.

用法:
    from tikhub_cli.monitor import Monitor, check_once

    # 单次检查
    result = check_once()

    # 持续监控
    m = Monitor(interval_minutes=15)
    m.run_forever()

存储:
    ~/.tikhub/
    ├── monitor.db              — 监控数据库（含 account_key 字段）
    ├── monitor_<account>.pid   — 各账号守护进程 PID
    ├── monitor_<account>.log   — 各账号日志
    ├── accounts/<account>/downloads/  — 各账号下载目录
    └── accounts/<account>/auth.json  — 各账号登录态
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tikhub_cli import accounts

# ═══════════════════════════════════════
# 路径
# ═══════════════════════════════════════

BASE = accounts.BASE
DB_PATH = BASE / "monitor.db"
DOWNLOADS_DIR = BASE / "downloads"  # fallback, prefer accounts.downloads_dir()

def _pid_path(account: str | None = None) -> Path:
    key = account or accounts.get_account()
    return BASE / f"monitor_{key}.pid"

def _log_path(account: str | None = None) -> Path:
    key = account or accounts.get_account()
    return BASE / f"monitor_{key}.log"

# 抖音 API
HOME_URL = "https://www.douyin.com/"
FAV_TAB_URL = "https://www.douyin.com/user/self?showTab=favorite_collection"

RESPONSE_SOURCES = {
    "/aweme/v1/web/aweme/listcollection/": "favorites",
    "/aweme/v1/web/aweme/favorite/": "likes",
}

DEFAULT_INTERVAL = 15  # 分钟
MIN_INTERVAL = 10       # 最小间隔，避免风控


# ═══════════════════════════════════════
# 数据库
# ═══════════════════════════════════════

class MonitorDB:
    """追踪已知视频和下载状态."""

    def __init__(self, path: str | Path = DB_PATH):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as db:
            # 检查并迁移旧表
            cols = [r[1] for r in db.execute("PRAGMA table_info(known_videos)")]
            if "account_key" not in cols:
                db.execute("ALTER TABLE known_videos ADD COLUMN account_key TEXT NOT NULL DEFAULT 'default'")
                # 重建主键
                db.execute("""
                    CREATE TABLE IF NOT EXISTS known_videos_new (
                        aweme_id TEXT NOT NULL, account_key TEXT NOT NULL DEFAULT 'default',
                        source TEXT NOT NULL, desc TEXT, author TEXT,
                        first_seen REAL NOT NULL, downloaded INTEGER DEFAULT 0,
                        file_path TEXT, error TEXT,
                        PRIMARY KEY (account_key, aweme_id)
                    )
                """)
                db.execute("INSERT OR IGNORE INTO known_videos_new SELECT aweme_id, 'default', source, desc, author, first_seen, downloaded, file_path, error FROM known_videos")
                db.execute("DROP TABLE known_videos")
                db.execute("ALTER TABLE known_videos_new RENAME TO known_videos")

            db.execute("""
                CREATE TABLE IF NOT EXISTS known_videos (
                    aweme_id    TEXT NOT NULL,
                    account_key TEXT NOT NULL DEFAULT 'default',
                    source      TEXT NOT NULL,
                    desc        TEXT,
                    author      TEXT,
                    first_seen  REAL NOT NULL,
                    downloaded  INTEGER DEFAULT 0,
                    file_path   TEXT,
                    error       TEXT,
                    PRIMARY KEY (account_key, aweme_id)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_account ON known_videos(account_key)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_source ON known_videos(source)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_downloaded ON known_videos(downloaded)")

            # 检查 check_log 迁移
            cols2 = [r[1] for r in db.execute("PRAGMA table_info(check_log)")]
            if "account_key" not in cols2:
                db.execute("ALTER TABLE check_log ADD COLUMN account_key TEXT NOT NULL DEFAULT 'default'")

            db.execute("""
                CREATE TABLE IF NOT EXISTS check_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at  REAL NOT NULL,
                    account_key TEXT NOT NULL DEFAULT 'default',
                    source      TEXT NOT NULL,
                    new_count   INTEGER DEFAULT 0,
                    downloaded  INTEGER DEFAULT 0,
                    failed      INTEGER DEFAULT 0,
                    error       TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_chk_account ON check_log(account_key)")
            db.commit()

    def is_known(self, aweme_id: str, account_key: str = "default") -> bool:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT 1 FROM known_videos WHERE aweme_id = ? AND account_key = ?",
                (aweme_id, account_key),
            ).fetchone()
            return row is not None

    def mark_seen(self, aweme_id: str, source: str, account_key: str = "default",
                  desc: str = "", author: str = ""):
        with sqlite3.connect(self.path) as db:
            db.execute(
                """INSERT OR IGNORE INTO known_videos
                   (aweme_id, account_key, source, desc, author, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (aweme_id, account_key, source, desc, author, time.time()),
            )
            db.commit()

    def mark_downloaded(self, aweme_id: str, file_path: str, account_key: str = "default"):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE known_videos SET downloaded=1, file_path=? WHERE aweme_id=? AND account_key=?",
                (file_path, aweme_id, account_key),
            )
            db.commit()

    def mark_failed(self, aweme_id: str, error: str, account_key: str = "default"):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE known_videos SET downloaded=-1, error=? WHERE aweme_id=? AND account_key=?",
                (error[:500], aweme_id, account_key),
            )
            db.commit()

    def get_failed(self, limit: int = 10, account_key: str = "default") -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT aweme_id, source, error FROM known_videos "
                "WHERE downloaded = -1 AND account_key = ? ORDER BY first_seen DESC LIMIT ?",
                (account_key, limit),
            ).fetchall()
            return [{"aweme_id": r[0], "source": r[1], "error": r[2]} for r in rows]

    def log_check(self, source: str, new: int, downloaded: int, failed: int,
                  account_key: str = "default", error: str = ""):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO check_log "
                "(checked_at, account_key, source, new_count, downloaded, failed, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), account_key, source, new, downloaded, failed, error),
            )
            db.commit()

    def stats(self, account_key: str = "default") -> dict:
        with sqlite3.connect(self.path) as db:
            total = db.execute(
                "SELECT COUNT(*) FROM known_videos WHERE account_key=?",
                (account_key,),
            ).fetchone()[0]
            got = db.execute(
                "SELECT COUNT(*) FROM known_videos WHERE downloaded=1 AND account_key=?",
                (account_key,),
            ).fetchone()[0]
            failed = db.execute(
                "SELECT COUNT(*) FROM known_videos WHERE downloaded=-1 AND account_key=?",
                (account_key,),
            ).fetchone()[0]
            last = db.execute(
                "SELECT checked_at, new_count, downloaded FROM check_log "
                "WHERE account_key=? ORDER BY id DESC LIMIT 1",
                (account_key,),
            ).fetchone()
            return {
                "total_seen": total,
                "downloaded": got,
                "failed": failed,
                "last_check": last[0] if last else None,
                "last_new": last[1] if last else 0,
                "last_downloaded": last[2] if last else 0,
            }


# ═══════════════════════════════════════
# 采集逻辑（轻量版 — 只取第一页）
# ═══════════════════════════════════════

def _ensure_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise RuntimeError("需要 playwright。安装: pip install 'tikhub-cli[douyin]'")


def _load_auth(account: str | None = None) -> dict:
    path = accounts.auth_path(account)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    return {}


def _extract_cookie(auth: dict, name: str) -> str:
    for c in auth.get("cookies") or []:
        if c.get("name") == name:
            return str(c.get("value") or "")
    return ""


def _has_session(auth: dict) -> bool:
    return bool(_extract_cookie(auth, "sessionid") or _extract_cookie(auth, "sessionid_ss"))


def _safe_name(s: str, max_len: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(s or "video"))
    s = re.sub(r"[\s_]+", "_", s).strip(" ._")
    return s[:max_len].rstrip(" ._")


def check_once(sources: list[str] | None = None, account: str | None = None) -> dict:
    """执行一次检查，返回新发现的视频.

    Args:
        sources: 要检查的来源，默认 ["favorites", "likes"]
        account: 账号名
    """
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    key = account or accounts.get_account()
    sources = sources or ["favorites", "likes"]
    db = MonitorDB()
    auth = _load_auth(key)

    if not _has_session(auth):
        return {"success": False, "error": "未登录。请先运行: tikhub login"}

    results: dict[str, dict] = {}
    all_new_items: list[dict] = []

    with sync_playwright() as pw:
        # 启动浏览器
        profile = accounts.browser_profile_dir(key)

        try:
            ctx = pw.chromium.launch_persistent_context(
                channel="chrome",
                user_data_dir=str(profile),
                headless=True,
                locale="zh-CN",
                accept_downloads=False,
            )
        except Exception:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
                locale="zh-CN",
                accept_downloads=False,
            )

        cookies = auth.get("cookies") or []
        if cookies:
            ctx.add_cookies(cookies)

        state = ctx.storage_state()
        if not _has_session(state):
            ctx.close()
            return {"success": False, "error": "登录态已过期。请重新运行: tikhub login"}

        sec_uid = _extract_cookie(state, "sec_user_id") or auth.get("sec_user_id", "")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        captured: dict[str, list[dict]] = {"favorites": [], "likes": []}
        got_pages: dict[str, bool] = {s: False for s in sources}

        def handle_response(resp: Any):
            path = urlparse(resp.url).path
            source = next(
                (v for suffix, v in RESPONSE_SOURCES.items() if path.endswith(suffix)),
                None,
            )
            if source is None or source not in sources or got_pages[source]:
                return
            try:
                data = resp.json()
                if resp.status != 200 or data.get("status_code") not in (None, 0):
                    return
                items = data.get("aweme_list") or data.get("aweme_list_data") or []
                captured[source].extend(items)
                got_pages[source] = True
            except Exception:
                pass

        page.on("response", handle_response)

        # 打开收藏页
        page.goto(FAV_TAB_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(8_000)

        # 如果喜欢 tab 没触发
        if "likes" in sources and not got_pages["likes"]:
            if sec_uid:
                page.goto(
                    f"https://www.douyin.com/user/{sec_uid}?showTab=like",
                    wait_until="domcontentloaded", timeout=60_000,
                )
                page.wait_for_timeout(8_000)
            else:
                likes_btn = page.get_by_text("喜欢", exact=True)
                if likes_btn.count() == 1:
                    likes_btn.click()
                    page.wait_for_timeout(5_000)

        page.remove_listener("response", handle_response)
        ctx.close()

        # 分析结果
        for source in sources:
            items = captured.get(source, [])
            new_items = []
            for item in items:
                aweme = item.get("aweme_info", item)
                aweme_id = str(aweme.get("aweme_id", ""))
                if not aweme_id:
                    continue
                if db.is_known(aweme_id, account_key=key):
                    continue
                desc = (aweme.get("desc") or "")[:100]
                author = (aweme.get("author", {}).get("nickname") or "")[:30]
                db.mark_seen(aweme_id, source, account_key=key, desc=desc, author=author)
                new_items.append(item)

            results[source] = {"pages": 1, "total": len(items), "new": len(new_items)}
            all_new_items.extend(new_items)
            db.log_check(source, len(new_items), 0, 0)

    total_new = sum(r["new"] for r in results.values())
    return {
        "success": True,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "sources": results,
        "total_new": total_new,
        "items": all_new_items,
    }


def download_new(items: list[dict], out_dir: str | None = None, account: str | None = None) -> dict:
    """下载新发现的视频到本地."""
    import requests

    key = account or accounts.get_account()
    out = Path(out_dir or accounts.downloads_dir(key)).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    auth = _load_auth(key)
    cookies = {
        c["name"]: c["value"]
        for c in (auth.get("cookies") or [])
        if c.get("name") and c.get("value")
    }

    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
    })

    db = MonitorDB()
    success = failed = 0

    for i, item in enumerate(items, 1):
        aweme = item.get("aweme_info", item)
        aweme_id = str(aweme.get("aweme_id", ""))
        desc = (aweme.get("desc") or "")[:40]
        author = (aweme.get("author", {}).get("nickname") or "unknown")[:16]
        safe_name = _safe_name(f"{author}_{desc}")
        target = out / f"{safe_name}.mp4"

        # 提取视频 URL
        video = aweme.get("video") or {}
        play = video.get("play_addr") or video.get("play_addr_h264") or {}
        urls = play.get("url_list") or []
        if not urls:
            db.mark_failed(aweme_id, "无视频 URL", account_key=key)
            failed += 1
            continue

        if target.exists() and target.stat().st_size > 1024:
            db.mark_downloaded(aweme_id, str(target), account_key=key)
            success += 1
            continue

        try:
            resp = session.get(urls[0], stream=True, timeout=(15, 120))
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").lower()
            if "text/html" in ct or "application/json" in ct:
                raise RuntimeError("资源地址已过期")

            with open(target, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if chunk:
                        f.write(chunk)

            if target.stat().st_size < 1024:
                target.unlink()
                raise RuntimeError("下载内容过小")

            db.mark_downloaded(aweme_id, str(target), account_key=key)
            success += 1
        except Exception as e:
            db.mark_failed(aweme_id, str(e), account_key=key)
            if target.exists():
                target.unlink()
            failed += 1

    return {"success": success, "failed": failed, "total": len(items)}


# ═══════════════════════════════════════
# 守护循环
# ═══════════════════════════════════════

class Monitor:
    def __init__(self, interval_minutes: int = DEFAULT_INTERVAL, account: str | None = None):
        self.interval = max(interval_minutes, MIN_INTERVAL) * 60
        self.account = account or accounts.get_account()
        self._running = False
        self._log_file: Any = None

    def _log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        if self._log_file:
            self._log_file.write(line + "\n")
            self._log_file.flush()

    def _one_tick(self):
        self._log("检查新视频...")
        try:
            result = check_once(account=self.account)
        except Exception as e:
            self._log(f"  检查失败: {e}")
            return

        if not result.get("success"):
            self._log(f"  检查失败: {result.get('error', '未知错误')}")
            return

        total_new = result.get("total_new", 0)
        sources = result.get("sources", {})
        for name, info in sources.items():
            self._log(f"  {name}: {info['total']} 个总计, {info['new']} 个新视频")

        if total_new == 0:
            self._log("  无新视频")
            return

        items = result.get("items", [])
        self._log(f"  开始下载 {len(items)} 个新视频...")
        try:
            dl = download_new(items, account=self.account)
            self._log(f"  下载完成: 成功 {dl['success']}, 失败 {dl['failed']}")
        except Exception as e:
            self._log(f"  下载异常: {e}")

    def run_forever(self):
        self._running = True
        log_p = _log_path(self.account)
        self._log_file = open(log_p, "a", encoding="utf-8")
        self._log("===== 监控已启动 =====")
        self._log(f"账号: {self.account} | 间隔: {self.interval // 60} 分钟 | 下载目录: {accounts.downloads_dir(self.account)}")

        def _stop(signum, frame):
            self._log("收到停止信号，正在退出...")
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        try:
            self._one_tick()  # 启动时立即检查一次
            while self._running:
                next_check = datetime.now() + timedelta(seconds=self.interval)
                self._log(f"下次检查: {next_check.strftime('%H:%M:%S')}")
                for _ in range(self.interval):
                    if not self._running:
                        break
                    time.sleep(1)
                if self._running:
                    self._one_tick()
        finally:
            if self._log_file:
                self._log("===== 监控已停止 =====")
                self._log_file.close()


# ═══════════════════════════════════════
# 守护进程管理
# ═══════════════════════════════════════

def daemon_start(interval_minutes: int = DEFAULT_INTERVAL, account: str | None = None) -> dict:
    """后台启动守护进程."""
    key = account or accounts.get_account()
    pid_p = _pid_path(key)
    log_p = _log_path(key)

    if pid_p.exists():
        old_pid = int(pid_p.read_text().strip())
        try:
            os.kill(old_pid, 0)
            return {"success": False, "error": f"守护进程已在运行 (PID: {old_pid}, 账号: {key})"}
        except OSError:
            pid_p.unlink()

    # fork
    pid = os.fork()
    if pid > 0:
        # 父进程返回
        pid_p.write_text(str(pid))
        return {"success": True, "pid": pid, "account": key, "log": str(log_p)}

    # 子进程
    os.setsid()
    # 重定向 stdout/stderr
    sys.stdout = open(log_p, "a")
    sys.stderr = sys.stdout

    monitor = Monitor(interval_minutes=interval_minutes, account=key)
    monitor.run_forever()
    os._exit(0)


def daemon_stop(account: str | None = None) -> dict:
    """停止守护进程."""
    key = account or accounts.get_account()
    pid_p = _pid_path(key)

    if not pid_p.exists():
        return {"success": False, "error": f"守护进程未运行 (账号: {key})"}

    pid = int(pid_p.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        pid_p.unlink()
        return {"success": True, "pid": pid, "account": key}
    except OSError as e:
        pid_p.unlink()
        return {"success": False, "error": f"无法停止进程 {pid}: {e}"}


def daemon_status(account: str | None = None) -> dict:
    """查询守护进程状态."""
    key = account or accounts.get_account()
    db = MonitorDB()
    stats = db.stats(account_key=key)

    running = False
    pid = None
    pid_p = _pid_path(key)
    if pid_p.exists():
        try:
            pid = int(pid_p.read_text().strip())
            os.kill(pid, 0)
            running = True
        except (OSError, ValueError):
            pass

    log_p = _log_path(key)
    tail = ""
    if log_p.exists():
        try:
            lines = log_p.read_text().splitlines()
            tail = "\n".join(lines[-5:])
        except Exception:
            pass

    return {
        "running": running,
        "pid": pid,
        "account": key,
        "log_path": str(log_p),
        "db_path": str(DB_PATH),
        "log_tail": tail,
        **stats,
    }
