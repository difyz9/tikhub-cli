# TikHub CLI

一站式 CLI 工具：抖音个人数据采集 + TikHub 多平台 API + 通用链接下载 + 内容分类。

## 环境要求

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Python 3.10+ | 运行环境 | — |
| Microsoft Edge 或 Chromium | `tikhub login` / `fetch` | Edge 已预装在 Windows/macOS；或 `python3 -m playwright install chromium` |
| FFmpeg | `tikhub extract`（帧截图） | `brew install ffmpeg` / `apt install ffmpeg` |

## 安装

```bash
cd tikhub-cli

# 基础版（TikHub API + 下载 + 帧提取 + 分类 + 报告）
pip install -e .

# 抖音个人数据采集（login / fetch）
pip install -e ".[douyin]"

# 通用链接下载（yt-dlp）
pip install -e ".[link]"

# 全部功能
pip install -e ".[full]"
```

TikHub API 功能需要注册获取 API Key：https://tikhub.io

```bash
export TIKHUB_API_KEY="your_key_here"
```

## 使用

### 1. 抖音个人数据采集

采集自己账号的点赞和收藏视频到本地。

```bash
# 扫码登录（会打开 Edge 浏览器窗口）
tikhub login

# 查看登录状态
tikhub status

# 采集收藏 + 点赞，各 3 页，自动下载
tikhub fetch all

# 只采集点赞，5 页
tikhub fetch likes -p 5

# 不下载，只采集数据
tikhub fetch all --no-download

# 指定下载目录，最多 20 个视频
tikhub fetch all -o ~/Desktop/douyin -n 20

# 无头模式（后台运行，但更容易触发验证码）
tikhub fetch all --headless
```

**工作流程**：
1. `tikhub login` — 打开 Edge 窗口，手动扫码登录。Cookie 保存到 `~/.tikhub/auth.json`
2. `tikhub fetch` — 打开浏览器，滚动收藏/喜欢页面，拦截抖音 API 响应，提取视频地址，逐个下载到 `~/.tikhub/downloads/`

下载的视频命名格式：`作者_文案摘要.mp4`

### 2. TikHub API 搜索

覆盖抖音、Threads、YouTube、知乎、Reddit 五个平台。

```bash
# 抖音视频搜索
tikhub search 猫咪
tikhub search 猫咪 -n 20               # 显示 20 条

# 切换端点
tikhub search AI -e dy_user_search      # 用户搜索
tikhub search "hello" -e threads_post   # Threads 帖子
tikhub search 科普 -e dy_general_search # 综合搜索

# JSON 输出（适合管道/脚本）
tikhub search 猫咪 --json | jq .

# 跳过缓存，强制实时请求
tikhub search 猫咪 --no-cache

# 查看所有可用端点
tikhub endpoints
tikhub endpoints -p douyin
tikhub endpoints -p youtube
```

### 3. 通用链接下载

用 yt-dlp 下载抖音、B站、YouTube、小红书等任意公开链接。

```bash
# 下载
tikhub link "https://v.douyin.com/xxxx/"
tikhub link "https://www.bilibili.com/video/BV..."
tikhub link "https://youtu.be/xxxx" -o ./dl

# 只解析不下载（查看有哪些格式）
tikhub link "https://b23.tv/xxxx" --resolve-only

# 不合并音视频流（需 FFmpeg）
tikhub link "https://youtu.be/xxxx" --no-merge
```

### 4. 直接 URL 下载

并发分段下载，8 workers / 1MB chunk / 3 次重试，支持断点续传。

```bash
tikhub download https://example.com/video.mp4
tikhub download https://... -o ./out/v.mp4
tikhub download https://... -q          # 静默模式
```

### 5. 视频帧提取

从视频中截取关键帧（需要 FFmpeg）。

```bash
tikhub extract video.mp4                # 默认 5 帧
tikhub extract video.mp4 -n 10          # 10 帧均匀分布
tikhub extract video.mp4 -t 1.5 -t 5.0 -t 12.3  # 指定时间点
tikhub extract video.mp4 -o ./frames    # 输出到指定目录
```

### 6. 视频详情解析

通过分享链接获取视频元数据（需要 TikHub API Key）。

```bash
tikhub info "https://v.douyin.com/xxxx/"
tikhub info "https://v.douyin.com/xxxx/" --no-cache
```

### 7. 内容分类

17 类赛道自动分类（颜值/舞蹈/二次元/游戏/美食/知识...）。

```bash
# 从文件读取
tikhub classify -f results.json

# 管道输入
cat results.json | tikhub classify --stdin
```

每行一个 JSON 对象，或整个 JSON 数组均可。分类依赖 `desc`/`title` 和 `tags` 字段。

### 8. HTML 报告

从 JSON 数据生成离线 HTML 报告，自动分类 + 统计卡片 + 数据表格。

```bash
tikhub report "搜索猫咪" -f results.json
tikhub report "我的数据" -f data.json -o my_report.html
```

用浏览器打开即可查看，无需联网。

### 9. 缓存管理

TikHub API 的 SQLite 缓存。

```bash
tikhub cache stats                      # 查看统计
tikhub cache clear                      # 清除全部
tikhub cache clear -e dy_video_search   # 按端点清除
```

## 项目结构

```
tikhub-cli/
├── pyproject.toml          # 打包配置
├── README.md
└── tikhub_cli/
    ├── cli.py              # Click CLI（12 子命令）
    ├── sdk.py              # TikHub SDK（24 端点 + SQLite 缓存）
    ├── collector.py        # Playwright 抖音采集（登录 + 收藏 + 点赞 + 下载）
    ├── downloader.py       # 并发下载引擎（8w/1MB/3retry + 断点续传）
    ├── resolver.py         # yt-dlp 通用链接解析 + 下载
    ├── classifier.py       # 17 类赛道内容分类
    ├── extractor.py        # FFmpeg 视频帧提取
    └── report.py           # 离线 HTML 报告生成
```

## 12 个子命令

| 命令 | 功能 | 需要 |
|------|------|------|
| `login` | 扫码登录抖音 | `[douyin]` + Edge/Chromium |
| `status` | 查看登录状态 | `[douyin]` |
| `fetch` | 采集点赞/收藏并下载 | `[douyin]` + Edge/Chromium |
| `search` | 多平台搜索 | `TIKHUB_API_KEY` |
| `info` | 分享链接解析 | `TIKHUB_API_KEY` |
| `link` | yt-dlp 通用链接下载 | `[link]` |
| `download` | 直接 URL 并发下载 | 无 |
| `extract` | 视频帧截图 | FFmpeg |
| `classify` | 17 类内容分类 | 无 |
| `report` | 生成 HTML 报告 | 无 |
| `cache` | 缓存管理 | 无 |
| `endpoints` | 列出 API 端点 | 无 |

## TikHub API 端点

| 平台 | 端点数 | 示例 |
|------|--------|------|
| 抖音 | 17 | 视频搜索、用户作品、评论、热搜、合集 |
| Threads | 2 | 帖子详情、评论 |
| YouTube | 2 | 字幕、帖子 |
| Zhihu | 1 | 用户文章 |
| Reddit | 1 | 活跃社区 |

## 数据存储

```
~/.tikhub/
├── auth.json              # 抖音登录态（Cookie + sec_user_id）
├── cache.db               # TikHub API SQLite 缓存
├── browser_profile/       # Edge 持久化用户目录（隔离，不影响日常浏览器）
└── downloads/             # 下载的视频文件
    ├── 作者_文案摘要1.mp4
    ├── 作者_文案摘要2.mp4
    └── ...
```
