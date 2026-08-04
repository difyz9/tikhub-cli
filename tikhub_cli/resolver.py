"""通用链接解析与下载 — yt-dlp 驱动，支持 抖音/B站/YouTube/小红书 等.

用法:
    from tikhub_cli.resolver import resolve, download_link

    info = resolve("https://v.douyin.com/xxxx/")
    print(info["title"], len(info["formats"]), "个格式")

    result = download_link("https://www.bilibili.com/video/BV...", "./out/")
"""

from __future__ import annotations

import os
import re
import shutil
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ═══════════════════════════════════════
# URL 检测
# ═══════════════════════════════════════

def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".bilibili.com") or host == "b23.tv":
        return "bilibili"
    if host in {"douyin.com", "iesdouyin.com", "v.douyin.com"} or \
       host.endswith(".douyin.com") or host.endswith(".iesdouyin.com"):
        return "douyin"
    if host.endswith(".youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith(".xiaohongshu.com") or host == "xhslink.com":
        return "xiaohongshu"
    if host.endswith(".twitter.com") or host in {"x.com", "t.co"}:
        return "twitter"
    if host.endswith(".instagram.com"):
        return "instagram"
    return (host.removeprefix("www.").split(".")[0] or "generic")[:64]


def validate_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ValueError(f"只支持 http/https 链接: {url}")
    host = p.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError("拒绝解析本机地址")
    try:
        import ipaddress
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise ValueError("拒绝解析内网/保留地址")
    except socket.gaierror as e:
        raise ValueError(f"域名无法解析: {host}") from e
    return url.strip()


# ═══════════════════════════════════════
# yt-dlp 解析
# ═══════════════════════════════════════

def resolve(url: str) -> dict[str, Any]:
    """用 yt-dlp 解析链接，返回标准化的 info dict."""
    url = validate_url(url)
    _ensure_ytdlp()

    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 30, "retries": 3, "extractor_retries": 2,
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return YoutubeDL.sanitize_info(info)


# ═══════════════════════════════════════
# 下载
# ═══════════════════════════════════════

def download_link(
    url: str,
    out_dir: str = ".",
    merge: bool = True,
) -> dict[str, Any]:
    """解析并下载链接，返回结果摘要.

    Args:
        url: 分享链接
        out_dir: 输出目录
        merge: 是否用 ffmpeg 合并音视频流（需安装 ffmpeg）

    Returns:
        {"success": bool, "platform": str, "title": str, "files": [...], "error": ""}
    """
    url = validate_url(url)
    platform = detect_platform(url)

    # 检查 ffmpeg
    ffmpeg_ok = bool(shutil.which("ffmpeg"))

    info = resolve(url)
    title = str(info.get("title") or "未命名")[:60]
    safe_title = _safe_filename(title)

    target = Path(out_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    template = str(target / f"{safe_title}.%(ext)s")

    _ensure_ytdlp()
    from yt_dlp import YoutubeDL

    fmt = "bv*+ba/b" if (merge and ffmpeg_ok) else "best[ext=mp4]/best"
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 30, "retries": 3,
        "format": fmt,
        "outtmpl": template,
        "merge_output_format": "mp4" if merge else None,
        "overwrites": False, "continuedl": True,
        "windowsfilenames": True,
    }

    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

        # 找输出文件
        files = sorted(
            p for p in target.glob(f"{safe_title}.*")
            if p.is_file() and p.suffix not in {".part", ".ytdl"}
        )
        if not files:
            raise RuntimeError("下载完成但未找到输出文件")

        return {
            "success": True,
            "platform": platform,
            "title": title,
            "files": [str(f) for f in files],
            "total_size": sum(f.stat().st_size for f in files),
            "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
        }
    except Exception as e:
        return {"success": False, "platform": platform, "title": title, "error": str(e)}


# ═══════════════════════════════════════
# 辅助
# ═══════════════════════════════════════

def _safe_filename(s: str, max_len: int = 60) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(s or "未命名"))
    s = re.sub(r"[\s_]+", "_", s).strip(" ._")
    return s[:max_len].rstrip(" ._")


def _ensure_ytdlp() -> None:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "需要 yt-dlp。安装: pip install 'tikhub-cli[link]'"
        ) from None


def list_extractors() -> list[str]:
    """列出 yt-dlp 支持的站点."""
    _ensure_ytdlp()
    from yt_dlp.extractor import list_extractors as _list
    return sorted(_list())
