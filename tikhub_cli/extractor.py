"""FFmpeg 帧提取 — 从视频中截图.

用法:
    import asyncio
    from tikhub_cli.extractor import extract

    frames = asyncio.run(extract("./video.mp4", count=5))
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Frame:
    path: str
    timestamp: float
    width: int = 0
    height: int = 0
    size: int = 0


async def extract(
    video: str,
    out_dir: str = ".",
    count: int = 5,
    timestamps: list[float] | None = None,
    max_width: int = 1080,
) -> list[Frame]:
    """从视频中提取帧.

    Args:
        video: 视频文件路径
        out_dir: 输出目录
        count: 均匀抽取帧数（timestamps 为 None 时生效）
        timestamps: 指定时间点（秒），优先级高于 count
        max_width: 输出图片最大宽度

    Returns:
        Frame 列表
    """
    if not os.path.exists(video):
        raise FileNotFoundError(f"视频不存在: {video}")

    dur = await _duration(video)
    if not dur or dur <= 0:
        raise ValueError(f"无法获取视频时长: {video}")

    ts_list = ([t for t in timestamps if 0 <= t < dur] if timestamps
               else [dur / (count + 1) * i for i in range(1, count + 1)])

    if not ts_list:
        ts_list = [dur / 2]

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video))[0]
    frames: list[Frame] = []

    for idx, ts in enumerate(ts_list):
        out = os.path.join(out_dir, f"{base}_f{idx + 1:03d}.jpg")
        ok = await _grab(video, out, ts, max_width)
        if ok:
            info = await _image_info(out)
            frames.append(Frame(path=out, timestamp=ts, **info))

    return frames


async def _grab(video: str, out: str, ts: float, max_w: int) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(ts),
        "-i", video,
        "-vframes", "1",
        "-q:v", "2",
        "-vf", f"scale='min({max_w},iw)':-1",
        out,
    ]
    try:
        p = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()
        return p.returncode == 0 and os.path.exists(out)
    except FileNotFoundError:
        raise RuntimeError("FFmpeg 未安装: brew install ffmpeg")


async def _duration(video: str) -> float | None:
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", video,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await p.communicate()
        return float(json.loads(stdout)["format"]["duration"])
    except Exception:
        return None


async def _image_info(path: str) -> dict:
    info = {"width": 0, "height": 0, "size": os.path.getsize(path)}
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await p.communicate()
        streams = json.loads(stdout).get("streams", [])
        if streams:
            info["width"] = streams[0].get("width", 0)
            info["height"] = streams[0].get("height", 0)
    except Exception:
        pass
    return info
