"""多账号路径隔离 — 每个抖音账号独立存储.

目录结构:
    ~/.tikhub/
    ├── accounts/
    │   ├── default/           # 默认账号
    │   │   ├── auth.json      # 登录态
    │   │   ├── browser_profile/  # Edge 持久化用户目录
    │   │   └── downloads/     # 下载的视频
    │   └── work/              # 另一个账号
    │       ├── auth.json
    │       ├── browser_profile/
    │       └── downloads/
    ├── monitor.db             # 监控数据库（多账号共用，按 account_key 分表）
    ├── monitor.pid            # 守护进程 PID
    ├── monitor.log            # 日志
    └── cache.db               # TikHub API 缓存
"""

from __future__ import annotations

from pathlib import Path

BASE = Path.home() / ".tikhub"

# 全局默认账号
_current_account = "default"


def set_account(name: str) -> str:
    global _current_account
    _current_account = _sanitize(name or "default")
    return _current_account


def get_account() -> str:
    return _current_account


def account_dir(account: str | None = None) -> Path:
    key = account or _current_account
    d = BASE / "accounts" / _sanitize(key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def auth_path(account: str | None = None) -> Path:
    return account_dir(account) / "auth.json"


def browser_profile_dir(account: str | None = None) -> Path:
    d = account_dir(account) / "browser_profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def downloads_dir(account: str | None = None) -> Path:
    d = account_dir(account) / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_accounts() -> list[str]:
    """列出所有已创建的账号."""
    ad = BASE / "accounts"
    if not ad.exists():
        return []
    return sorted(
        d.name for d in ad.iterdir()
        if d.is_dir() and (d / "auth.json").exists()
    )


def _sanitize(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    return "".join("_" if c in forbidden else c for c in name).strip(" ._")[:80]
