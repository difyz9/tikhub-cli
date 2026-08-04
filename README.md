# TikHub CLI

一站式 CLI 工具：抖音个人数据采集 + TikHub 多平台 API + 通用链接下载 + 图片逆向提示词 + 内容分类。

## 环境要求

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Python 3.10+ | 运行环境 | — |
| Chrome 或 Chromium | `tikhub login` / `fetch` / `monitor` | Chrome 已安装则直接使用；否则 `python3 -m playwright install chromium` |
| FFmpeg | `tikhub extract`（帧截图） | `brew install ffmpeg` / `apt install ffmpeg` |

## 安装

```bash
cd tikhub-cli

# 基础版（TikHub API + 下载 + 帧提取 + 分类 + 报告 + 图片逆向）
pip install -e .

# 抖音个人数据采集（login / fetch / monitor）
pip install -e ".[douyin]"

# 通用链接下载（yt-dlp）
pip install -e ".[link]"

# 全部功能
pip install -e ".[full]"
```

TikHub API 功能需要注册获取 API Key：https://tikhub.io
图片逆向提示词需要设置 NVIDIA API Key（或任意 OpenAI 兼容的 Key）：

```bash
export TIKHUB_API_KEY="your_key_here"
export REVERSE_API_KEY="nvapi-..."      # NVIDIA key 自动识别，无需其他配置
```

## 使用

### 1. 抖音个人数据采集

采集自己账号的点赞和收藏视频到本地。

```bash
# 扫码登录（会打开 Chrome 浏览器窗口）
tikhub login
tikhub login -a personal                  # 多账号：指定别名

# 查看登录状态
tikhub status
tikhub status -a personal

# 采集收藏 + 点赞，各 3 页，自动下载
tikhub fetch all
tikhub fetch likes -p 5                   # 只采集点赞，5 页
tikhub fetch all --no-download            # 只采集不下载
tikhub fetch all -o ~/Desktop/douyin -n 20  # 指定目录，最多 20 个
tikhub fetch all -a work                  # 指定账号
tikhub fetch all --headless               # 无头模式（可能触发验证）
```

**工作流程**：
1. `tikhub login` — 打开 Chrome 窗口，手动扫码登录。每个账号独立存储
2. `tikhub fetch` — 打开浏览器，滚动收藏/喜欢页面，拦截抖音 API 响应，提取视频地址，下载

下载的视频命名格式：`作者_文案摘要.mp4`

### 2. 实时监控（新增点赞/收藏自动下载）

守护进程持续检查新点赞/收藏，发现新视频立即下载。

```bash
# 单次检查
tikhub monitor

# 持续监控（前台，每 15 分钟，Ctrl+C 停止）
tikhub monitor --watch
tikhub monitor --watch -i 30              # 自定义间隔（分钟）

# 后台守护进程
tikhub monitor --daemon
tikhub monitor --daemon -a work -i 20     # 指定账号和间隔

# 查看 / 停止
tikhub monitor --status
tikhub monitor --status -a personal
tikhub monitor --stop
tikhub monitor --stop -a work
```

### 3. TikHub API 搜索

覆盖抖音、Threads、YouTube、知乎、Reddit 五个平台。

```bash
tikhub search 猫咪
tikhub search 猫咪 -n 20                  # 显示 20 条
tikhub search AI -e dy_user_search         # 用户搜索
tikhub search "hello" -e threads_post      # Threads 帖子
tikhub search 猫咪 --json | jq .           # JSON 输出
tikhub search 猫咪 --no-cache              # 跳过缓存

# 查看所有可用端点
tikhub endpoints
tikhub endpoints -p douyin
```

### 4. 通用链接下载

用 yt-dlp 下载抖音、B站、YouTube、小红书等任意公开链接。

```bash
tikhub link "https://v.douyin.com/xxxx/"
tikhub link "https://www.bilibili.com/video/BV..."
tikhub link "https://youtu.be/xxxx" -o ./dl
tikhub link "https://b23.tv/xxxx" --resolve-only   # 只解析不下载
```

### 5. 直接 URL 下载

并发分段下载，8 workers / 1MB chunk / 3 次重试，支持断点续传。

```bash
tikhub download https://example.com/video.mp4
tikhub download https://... -o ./out/v.mp4
tikhub download https://... -q               # 静默模式
```

### 6. 视频帧提取

从视频中截取关键帧（需要 FFmpeg）。

```bash
tikhub extract video.mp4                     # 默认 5 帧
tikhub extract video.mp4 -n 10               # 10 帧
tikhub extract video.mp4 -t 1.5 -t 5.0 -t 12.3  # 指定时间点
tikhub extract video.mp4 -o ./frames
```

### 7. 视频详情解析

通过分享链接获取视频元数据（需要 TikHub API Key）。

```bash
tikhub info "https://v.douyin.com/xxxx/"
```

### 8. 图片逆向提示词

上传图片，AI 分析并生成可用于 Stable Diffusion / Midjourney / DALL-E 复刻该图片风格的提示词。

```bash
# 设置 API Key（任选一种）
export REVERSE_API_KEY="nvapi-..."           # NVIDIA — 自动识别，无需其他配置
export REVERSE_API_KEY="sk-..."              # OpenAI — 自动走 OmniRoute
export REVERSE_API_KEY="..."                 # 其他 OpenAI 兼容服务

# 单张图片
tikhub reverse photo.jpg

# 批量
tikhub reverse a.png b.jpg c.webp

# 指定模型
tikhub reverse img.png -m openai/gpt-4o

# 自定义 API
tikhub reverse img.png --base-url https://api.openai.com/v1 --api-key sk-xxx

# JSON 输出（适合管道）
tikhub reverse img.png --json | jq .prompt
```

输出包含：中文提示词、英文提示词、风格标签、主体/场景/氛围/镜头/色彩等结构化字段。

### 9. 内容分类

17 类赛道自动分类（颜值/舞蹈/二次元/游戏/美食/知识/剧情/搞笑/音乐/萌宠/时尚/生活/情感/运动/影视/明星/其他）。

```bash
tikhub classify -f results.json
cat results.json | tikhub classify --stdin
```

### 10. HTML 报告

从 JSON 数据生成离线 HTML 报告，自动分类 + 统计卡片 + 数据表格。

```bash
tikhub report "搜索猫咪" -f results.json
tikhub report "我的数据" -f data.json -o my_report.html
```

### 11. 缓存管理

TikHub API 的 SQLite 缓存。

```bash
tikhub cache stats
tikhub cache clear
tikhub cache clear -e dy_video_search
```

## 项目结构

```
tikhub-cli/
├── pyproject.toml          # 打包配置
├── README.md
└── tikhub_cli/
    ├── cli.py              # Click CLI（14 子命令）
    ├── accounts.py         # 多账号路径隔离管理
    ├── sdk.py              # TikHub SDK（24 端点 + SQLite 缓存）
    ├── collector.py        # Playwright 抖音采集（login / fetch / status）
    ├── monitor.py          # 实时监控守护进程（monitor --watch / --daemon）
    ├── downloader.py       # 并发下载引擎（8w/1MB/3retry + 断点续传）
    ├── resolver.py         # yt-dlp 通用链接解析 + 下载
    ├── reverser.py         # 图片逆向提示词（NVIDIA / OpenAI / OmniRoute）
    ├── classifier.py       # 17 类赛道内容分类
    ├── extractor.py        # FFmpeg 视频帧提取
    └── report.py           # 离线 HTML 报告生成
```

## 14 个子命令

| 命令 | 功能 | 需要 |
|------|------|------|
| `login` | 扫码登录抖音 | `[douyin]` + Chrome |
| `status` | 查看登录状态 | `[douyin]` |
| `fetch` | 采集点赞/收藏并下载 | `[douyin]` + Chrome |
| `monitor` | 实时监控新点赞/收藏，自动下载 | `[douyin]` + Chrome |
| `search` | 多平台搜索 | `TIKHUB_API_KEY` |
| `info` | 分享链接解析 | `TIKHUB_API_KEY` |
| `link` | yt-dlp 通用链接下载 | `[link]` |
| `download` | 直接 URL 并发下载 | 无 |
| `reverse` | 图片逆向提示词 | `REVERSE_API_KEY` |
| `extract` | 视频帧截图 | FFmpeg |
| `classify` | 17 类内容分类 | 无 |
| `report` | 生成 HTML 报告 | 无 |
| `cache` | 缓存管理 | 无 |
| `endpoints` | 列出 API 端点 | 无 |

## 环境变量

| 变量 | 用途 | 适用命令 |
|------|------|----------|
| `TIKHUB_API_KEY` | TikHub API 密钥 | `search`, `info` |
| `REVERSE_API_KEY` | 图片逆向 LLM 密钥（`nvapi-` 前缀自动走 NVIDIA） | `reverse` |
| `NVIDIA_API_KEY` | NVIDIA NIM 密钥（备选） | `reverse` |
| `OPENAI_API_KEY` | OpenAI 密钥（备选） | `reverse` |

## 数据存储

```
~/.tikhub/
├── accounts/               # 多账号隔离
│   ├── default/            # 默认账号
│   │   ├── auth.json       # 登录态（Cookie + sec_user_id）
│   │   ├── browser_profile/# Chrome 持久化用户目录
│   │   └── downloads/      # 下载的视频
│   └── work/               # 另一个账号
│       └── ...
├── monitor.db              # 监控去重数据库
├── monitor_default.pid     # 守护进程 PID
├── monitor_default.log     # 守护进程日志
└── cache.db                # TikHub API SQLite 缓存
```
