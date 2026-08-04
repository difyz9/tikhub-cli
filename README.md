# TikHub CLI

TikHub API 命令行工具 + 通用链接下载 + 内容分类 — 一站式数据获取。

## 安装

```bash
cd tikhub-cli
pip install -e .              # 基础版
pip install -e ".[link]"      # 含 yt-dlp 链接下载
pip install -e ".[full]"      # 全部功能

# 设置 API Key（可选，搜索/API 功能需要）
export TIKHUB_API_KEY="your_key_here"
```

注册: https://tikhub.io

## 9 个子命令

```bash
# ── 搜索 (TikHub API) ──
tikhub search 猫咪                          # 抖音视频搜索（默认）
tikhub search 猫咪 -e dy_user_search        # 用户搜索
tikhub search "AI" --json                   # JSON 输出

# ── 直接下载 ──
tikhub download https://example.com/video.mp4
tikhub download https://... -o ./v.mp4 -q   # 静默模式

# ── 通用链接 (yt-dlp) ──
tikhub link "https://v.douyin.com/..."            # 下载
tikhub link "https://b23.tv/..." --resolve-only   # 只解析
tikhub link "https://youtu.be/..." -o ./dl        # 指定目录

# ── 帧提取 ──
tikhub extract video.mp4 -n 10
tikhub extract video.mp4 -t 1.5 -t 5.0 -t 12.3    # 指定时间点

# ── 视频详情 (TikHub API) ──
tikhub info "https://v.douyin.com/xxxx/"

# ── 内容分类 ──
tikhub classify -f results.json          # 从文件
cat results.json | tikhub classify --stdin  # 管道输入

# ── HTML 报告 ──
tikhub report "搜索猫咪" -f results.json
tikhub report "我的数据" -f data.json -o my_report.html

# ── 缓存管理 ──
tikhub cache stats
tikhub cache clear -e dy_video_search

# ── 端点列表 ──
tikhub endpoints -p douyin
```

## 项目结构

```
tikhub-cli/
├── pyproject.toml          # 打包配置
├── README.md
└── tikhub_cli/
    ├── cli.py              # Click CLI（9 子命令）
    ├── sdk.py              # TikHub SDK（24 端点 + SQLite 缓存）
    ├── downloader.py       # 并发下载引擎（8w/1MB/3retry + 断点续传）
    ├── extractor.py        # FFmpeg 帧提取
    ├── resolver.py         # yt-dlp 通用链接解析 + 下载
    ├── classifier.py       # 17 类赛道内容分类
    └── report.py           # 离线 HTML 报告生成
```

## 端点覆盖

| 平台 | 端点数 | 示例 |
|------|--------|------|
| 抖音 | 17 | 视频搜索、用户作品、评论、热搜、合集 |
| Threads | 2 | 帖子详情、评论 |
| YouTube | 2 | 字幕、帖子 |
| Zhihu | 1 | 用户文章 |
| Reddit | 1 | 活跃社区 |

## 依赖

- `httpx`, `click`, `rich` — 核心
- `yt-dlp`, `requests`（可选）— `tikhub link`
- `FFmpeg`（可选）— `tikhub extract`
