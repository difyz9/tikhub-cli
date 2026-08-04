"""图片逆向提示词 — 上传图片，AI 分析并生成可复刻风格的提示词.

用法:
    from tikhub_cli.reverser import reverse

    result = reverse("image.png")
    print(result["prompt"])
    print(result["prompt_en"])

支持的后端:
    - OmniRoute (OpenAI 兼容) — 默认
    - NVIDIA NIM — nvidia_api_key 环境变量
    - 任意 OpenAI 兼容 API — base_url + api_key

环境变量:
    REVERSE_API_KEY    — API Key（默认从 memory 读取 OmniRoute key）
    REVERSE_BASE_URL   — API 地址（默认 OmniRoute）
    REVERSE_MODEL      — 模型名（默认 gpt-4o-mini）
    NVIDIA_API_KEY     — NVIDIA NIM 备选
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

# ═══════════════════════════════════
# 默认配置
# ═══════════════════════════════════

DEFAULT_BASE = "http://43.160.253.168:20128/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "meta/llama-3.2-11b-vision-instruct"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048

# System Prompt — 告诉 LLM 如何分析图片
SYSTEM_PROMPT = """你是一个专业的提示词逆向工程师。你的任务是基于用户提供的图片，生成一段高质量的、可用于 AI 图像生成模型（如 Stable Diffusion、Midjourney、DALL-E）复刻该图片的提示词。

请严格按照以下 JSON 格式返回，不要包含其他内容：

{
  "prompt": "详细的中文提示词，包含主体、环境、光线、构图、画质、风格等完整描述，可直接用于 AI 绘图",
  "prompt_en": "对应的英文提示词版本，适合 Stable Diffusion / Midjourney 使用",
  "style_tags": ["标签1", "标签2", "标签3"],
  "scene": "场景类型",
  "subject": "主体描述",
  "aspect_ratio": "画面比例推测",
  "colors": ["主要颜色1", "主要颜色2"],
  "mood": "情绪/氛围",
  "camera": "镜头/视角描述"
}

分析要点：
1. 主体（人物/动物/物体）的详细特征
2. 场景/背景环境
3. 光线效果（自然光、人造光、黄金时刻等）
4. 构图和镜头视角
5. 色彩色调
6. 画质/风格（摄影、插画、3D 等）
7. 情绪氛围"""


# ═══════════════════════════════════
# 核心函数
# ═══════════════════════════════════

def reverse(
    image: str | Path | bytes,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """从图片逆向生成提示词.

    Args:
        image: 图片路径 或 bytes
        model: 模型名（默认从环境变量或 DEFAULT_MODEL）
        base_url: API 地址
        api_key: API Key
        max_tokens: 最大 token
        temperature: 温度

    Returns:
        {prompt, prompt_en, style_tags, scene, subject,
         aspect_ratio, colors, mood, camera}
    """
    # 解析图片
    if isinstance(image, (str, Path)):
        path = Path(image).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"图片不存在: {path}")
        ext = path.suffix.lower().lstrip(".")
        mime = _mime_type(ext)
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
    elif isinstance(image, bytes):
        data = base64.b64encode(image).decode()
        mime = "image/png"
        ext = "png"
    else:
        raise TypeError(f"不支持的类型: {type(image)}")

    image_url = f"data:{mime};base64,{data}"

    # 配置
    key = api_key or os.getenv("REVERSE_API_KEY") or os.getenv("NVIDIA_API_KEY") or os.getenv("OPENAI_API_KEY", "")

    # 自动检测 NVIDIA key → 切换端点
    if key.startswith("nvapi-"):
        base_url = base_url or os.getenv("REVERSE_BASE_URL", NVIDIA_BASE)
        model = model or os.getenv("REVERSE_MODEL", NVIDIA_MODEL)
    else:
        base_url = (base_url or os.getenv("REVERSE_BASE_URL", DEFAULT_BASE)).rstrip("/")
        model = model or os.getenv("REVERSE_MODEL", DEFAULT_MODEL)

    url = base_url.rstrip("/")

    # 调用 LLM
    raw = _call_vision_api(url, key, model, image_url, max_tokens, temperature)

    # 解析
    result = _parse_response(raw)

    # 中英互译兜底
    zh = (result.get("prompt") or "").strip()
    en = (result.get("prompt_en") or "").strip()
    if not zh and en:
        result["prompt"] = _translate(url, key, en, "zh", model)
    elif not en and zh:
        result["prompt_en"] = _translate(url, key, zh, "en", model)

    return result


def _call_vision_api(
    base_url: str, api_key: str, model: str,
    image_url: str, max_tokens: int, temperature: float,
) -> str:
    """调用 OpenAI 兼容的 Vision API."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": "请仔细观察这张图片，按照 System Prompt 的 JSON 格式返回分析结果。"},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"API 返回异常: {json.dumps(data, ensure_ascii=False)[:500]}")


def _parse_response(text: str) -> dict[str, Any]:
    """从 LLM 回复中解析 JSON."""
    text = text.strip()
    # 移除 ```json ... ``` 包裹
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 兜底：提取第一个 { } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 全失败
    return {
        "prompt": text, "prompt_en": "", "style_tags": [],
        "scene": "", "subject": "", "aspect_ratio": "",
        "colors": [], "mood": "", "camera": "",
    }


def _translate(base_url: str, api_key: str, text: str, target: str, model: str) -> str:
    """调用 LLM 翻译提示词（使用纯文本模型更快更便宜）."""
    sys_prompt = {
        "zh": "将以下英文提示词翻译成中文。保持专业术语和风格关键词不变，只翻译描述性文字。直接返回翻译结果。",
        "en": "Translate the following Chinese prompt to English. Keep professional terms and style keywords. Return only the translation.",
    }

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt[target]},
                {"role": "user", "content": text},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return text


def _mime_type(ext: str) -> str:
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
        "gif": "image/gif", "bmp": "image/bmp",
    }.get(ext, "image/png")
