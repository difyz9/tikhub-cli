"""并发下载引擎 — 8 workers / 1MB chunks / 3次重试 / 断点续传.

用法:
    import asyncio
    from tikhub_cli.downloader import download

    result = asyncio.run(download("https://...", "./out.mp4"))
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

import httpx

# — 常量 —
WORKERS = 8
CHUNK = 1 * 1024 * 1024
MAX_RETRIES = 3
MIN_CHUNKED = 5 * 1024 * 1024
STATE_SUFFIX = ".dl.json"


@dataclass
class Result:
    success: bool
    path: str
    size: int = 0
    duration: float = 0
    error: str = ""


class DownloadError(Exception):
    pass


# ═══════════════════════════════════════
# 断点续传状态
# ═══════════════════════════════════════

class _State:
    def __init__(self, out: str, total: int, chunk: int):
        self.out = out
        self.total = total
        self.chunk = chunk
        self._done: set[int] = set()

    @property
    def path(self) -> str:
        return self.out + STATE_SUFFIX

    @property
    def done_chunks(self) -> int:
        return len(self._done)

    @property
    def done_bytes(self) -> int:
        return self.done_chunks * self.chunk

    @property
    def total_chunks(self) -> int:
        return (self.total + self.chunk - 1) // self.chunk

    @property
    def complete(self) -> bool:
        return self.done_chunks >= self.total_chunks

    def save(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"total": self.total, "chunk": self.chunk,
                        "done": sorted(self._done)}, f)

    @classmethod
    def load(cls, out: str) -> _State | None:
        sp = out + STATE_SUFFIX
        if not os.path.exists(sp):
            return None
        try:
            with open(sp) as f:
                d = json.load(f)
            s = cls(out, d["total"], d["chunk"])
            s._done = set(d.get("done", []))
            if s.complete:
                s.remove()
                return None
            return s
        except Exception:
            return None

    def remove(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)

    def mark(self, idx: int) -> None:
        self._done.add(idx)
        self.save()


# ═══════════════════════════════════════
# 下载入口
# ═══════════════════════════════════════

async def download(
    url: str,
    output: str,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> Result:
    """下载文件，自动选择分段/流式."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            head = await c.head(url)
            size = int(head.headers.get("content-length", "0"))
            ranges = head.headers.get("accept-ranges", "")

        if size > MIN_CHUNKED and ranges == "bytes":
            await _chunked(url, output, size, on_progress)
        else:
            await _stream(url, output, size, on_progress)

        fs = os.path.getsize(output) if os.path.exists(output) else 0
        return Result(success=True, path=output, size=fs,
                      duration=time.monotonic() - t0)
    except Exception as e:
        return Result(success=False, path=output, error=str(e),
                      duration=time.monotonic() - t0)


# ═══════════════════════════════════════
# 分段并发下载
# ═══════════════════════════════════════

async def _chunked(
    url: str, out: str, size: int,
    cb: Callable[[float], Awaitable[None]] | None,
) -> None:
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    state = _State.load(out) or _State(out, size, CHUNK)
    if state.total != size:
        state = _State(out, size, CHUNK)

    # 占位文件
    with open(out, "wb") as f:
        f.truncate(size)

    pending = [i for i in range(state.total_chunks) if i not in state._done]
    if not pending:
        return

    done_bytes = state.done_bytes
    lock = asyncio.Lock()

    # 进度协程
    stop = asyncio.Event()

    async def _progress_loop() -> None:
        while not stop.is_set():
            async with lock:
                pct = min(100, done_bytes / size * 100) if size else 0
            if cb:
                try:
                    await cb(pct)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass

    pg = asyncio.create_task(_progress_loop())

    # 下载单块
    async def _one(i: int) -> None:
        nonlocal done_bytes
        start = i * CHUNK
        end = min(start + CHUNK - 1, size - 1)
        part = f"{out}.part{i}"

        if os.path.exists(part) and os.path.getsize(part) == end - start + 1:
            async with lock:
                state.mark(i)
                done_bytes += end - start + 1
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
                    resp = await c.get(url, headers={"Range": f"bytes={start}-{end}"})
                    if resp.status_code != 206:
                        raise DownloadError(f"Range 返回 {resp.status_code}")
                    data = await resp.aread()

                if len(data) != end - start + 1:
                    raise DownloadError(f"块大小不符: 期望 {end - start + 1} 实际 {len(data)}")

                with open(part, "wb") as f:
                    f.write(data)

                async with lock:
                    state.mark(i)
                    done_bytes += len(data)
                return
            except Exception:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 * attempt)

        raise DownloadError(f"块 {i} 下载失败（{MAX_RETRIES}次重试）")

    sem = asyncio.Semaphore(WORKERS)

    async def _bounded(i: int) -> None:
        async with sem:
            await _one(i)

    await asyncio.gather(*[_bounded(i) for i in pending])
    stop.set()
    await pg

    # 合并
    with open(out, "wb") as fout:
        for i in range(state.total_chunks):
            part = f"{out}.part{i}"
            if os.path.exists(part):
                with open(part, "rb") as fin:
                    while buf := fin.read(8192):
                        fout.write(buf)
                os.remove(part)

    state.remove()


# ═══════════════════════════════════════
# 流式 Fallback
# ═══════════════════════════════════════

async def _stream(
    url: str, out: str, size: int,
    cb: Callable[[float], Awaitable[None]] | None,
) -> None:
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as c:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            got = 0
            with open(out, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
                    got += len(chunk)
                    if size and cb:
                        try:
                            await cb(got / size * 100)
                        except Exception:
                            pass
