"""图片逆向提示词 — 上传图片，AI 分析并生成可复刻风格的提示词.

用法:
    from tikhub_cli.reverser import reverse

    result = reverse("image.png")
    print(result["prompt"])          # 正向提示词 (English)
    print(result["prompt_cn"])       # 中文释义
    print(result["negative_prompt"]) # 反向提示词

支持的后端:
    - NVIDIA NIM — nvapi- 前缀自动识别
    - OmniRoute (OpenAI 兼容) — 默认
    - 任意 OpenAI 兼容 API — base_url + api_key

环境变量:
    REVERSE_API_KEY    — API Key（nvapi- 前缀自动走 NVIDIA）
    REVERSE_BASE_URL   — API 地址
    REVERSE_MODEL      — 模型名
    NVIDIA_API_KEY     — NVIDIA NIM 备选
    OPENAI_API_KEY     — OpenAI 备选
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

# ═══════════════════════════════════
# 默认配置
# ═══════════════════════════════════

DEFAULT_BASE = "http://43.160.253.168:20128/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
# NVIDIA_MODEL = "meta/llama-3.2-11b-vision-instruct"
NVIDIA_MODEL = "google/diffusiongemma-26b-a4b-it"

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 2048

# System Prompt — 专业提示词逆向工程师
SYSTEM_PROMPT = """你是一位专业的AI绘画提示词逆向工程师。根据用户上传的图片，逆向生成高质量AI绘图提示词。

【输出格式】（严格按此格式，不得遗漏）
---
**画面类型判断：** （写实摄影 / 日系二次元 / 赛博朋克 / 奇幻插画 / 其他）
**正向提示词（Positive Prompt）：**
（英文正向提示词，包含：画质词 > 主体描述 > 环境背景 > 构图镜头 > 风格词 > 色彩光影）
**反向提示词（Negative Prompt）：**
（至少15个逗号分隔的英文劣质元素关键词）
**参数建议：**
- 宽高比：建议值
- 模型建议：建议的AI模型
**中文释义：**
（正向提示词的中文翻译）
---

【约束】
- 客观分析视觉细节，不主观评价，不臆测图片外剧情
- 正向提示词包含具体服饰材质、光线方向、色彩倾向
- 如有人物需描述性别、年龄感、发型发色、服饰风格
- 二次元/插画风格使用对应画风关键词（anime style, illustration, flat color）"""

# keep it under 2000 chars for llama-vision context
assert len(SYSTEM_PROMPT) < 2000, f"System prompt too long: {len(SYSTEM_PROMPT)}"


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
        model: 模型名
        base_url: API 地址
        api_key: API Key
        max_tokens: 最大 token
        temperature: 温度

    Returns:
        {style_type, prompt, negative_prompt, params, prompt_cn}
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

    # 调用 LLM（最多重试 2 次，应对 NVIDIA 偶发超时）
    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            raw = _call_vision_api(url, key, model, image_url, max_tokens, temperature)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"3 次尝试均失败: {last_err}")

    # 解析结构化输出
    return _parse_response(raw)


def _call_vision_api(
    base_url: str, api_key: str, model: str,
    image_url: str, max_tokens: int, temperature: float,
) -> str:
    """调用 OpenAI 兼容的 Vision API."""
    import requests

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
                    {"type": "text", "text": "请严格按照 System Prompt 的格式分析这张图片，不要遗漏任何输出部分。"},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers=headers, json=body, timeout=(15, 120),
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"API 返回异常: {json.dumps(data, ensure_ascii=False)[:500]}")


def _parse_response(text: str) -> dict[str, Any]:
    """解析 LLM 的结构化 markdown 输出."""
    result: dict[str, Any] = {
        "style_type": "",
        "prompt": "",
        "negative_prompt": "",
        "params": {},
        "prompt_cn": "",
    }

    text = text.strip()
    if not text:
        return result

    # 去掉可能的 --- 包裹
    text = re.sub(r"^---+", "", text)
    text = re.sub(r"---+$", "", text)
    # 去掉 ### 转 **
    text = re.sub(r"^###\s*", "**", text, flags=re.MULTILINE)
    text = re.sub(r"^##\s*", "**", text, flags=re.MULTILINE)
    text = text.strip()

    # 提取各字段 — 同时匹配 ** 和 ### 格式
    result["style_type"] = _extract_section(text, r"\*{2,3}\s*画面类型判断[：:]\s*\*{0,3}", default="未识别")

    prompt = _extract_section(text, r"\*{2,3}\s*正向提示词[（(]Positive\s*Prompt[）)]?\s*\*{0,3}")
    if not prompt:
        prompt = _extract_section(text, r"\*{2,3}\s*正向提示词\s*\*{0,3}")
    result["prompt"] = prompt

    neg = _extract_section(text, r"\*{2,3}\s*反向提示词[（(]Negative\s*Prompt[）)]?\s*\*{0,3}")
    if not neg:
        neg = _extract_section(text, r"\*{2,3}\s*反向提示词\s*\*{0,3}")
    result["negative_prompt"] = neg

    params_text = _extract_section(text, r"\*{2,3}\s*参数建议[（(]如[有)）]?\s*\*{0,3}")
    if params_text:
        result["params"] = _parse_params(params_text)

    cn = _extract_section(text, r"\*{2,3}\s*中文释义[（(]供用户理解[）)]?\s*\*{0,3}")
    if not cn:
        cn = _extract_section(text, r"\*{2,3}\s*中文释义\s*\*{0,3}")
    result["prompt_cn"] = cn

    # 如果全部解析失败，降级：把原始文本当 prompt
    if not result["prompt"] and not result["prompt_cn"]:
        result["prompt"] = text
        result["prompt_cn"] = text

    return result


def _extract_section(text: str, pattern: str, default: str = "") -> str:
    """提取 **标题** 下的内容，到下一个 ** 标题或末尾."""
    idx = re.search(pattern, text)
    if not idx:
        return default

    remaining = text[idx.end():]
    # 找下一个 ** 标题（从行首开始）
    next_marker = re.search(r"\n\*\*[^\n]+\*\*", remaining)
    content = remaining[:next_marker.start()] if next_marker else remaining

    # 清理：去掉首尾换行、编号列表的序号、多余空格
    lines = content.strip().split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            cleaned.append("")
            continue
        # 去掉 "1. " "2. " 之类的编号前缀
        line = re.sub(r"^\d+\.\s*", "", line)
        # 去掉行首的 -
        line = re.sub(r"^-\s*", "", line)
        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    # 整理连续空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _parse_params(text: str) -> dict[str, str]:
    """解析参数建议为键值对."""
    params: dict[str, str] = {}
    for line in text.strip().split("\n"):
        line = line.strip().lstrip("- ")
        if not line:
            continue
        # 匹配 "宽高比：16:9" 或 "宽高比: 16:9"
        m = re.match(r"(.+?)[：:]\s*(.+)", line)
        if m:
            params[m.group(1).strip()] = m.group(2).strip()
    return params


def _mime_type(ext: str) -> str:
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
        "gif": "image/gif", "bmp": "image/bmp",
    }.get(ext, "image/png")
