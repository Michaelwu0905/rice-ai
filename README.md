# rice-ai

[![CI](https://github.com/Michaelwu0905/rice-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Michaelwu0905/rice-ai/actions/workflows/ci.yml)

专注 Linux ricing 的信息聚合 agent（第一阶段）。它抓取 **r/unixporn** 的优质 rice 帖，
扒出正文、评论、截图配色和 dots 仓库，再用 **DeepSeek** 生成中文「rice 卡片」，直接彩色输出到终端。

```
rice-ai · r/unixporn · week · 5 套方案 · deepseek-chat
1. Hyprland + Quickshell 的 Nothing OS 复刻 ────────────────────────────
原作 Twidlex- · 2026-10-25 · 原帖   dots github.com/Nebelung-cloud/glyph-rice
概况    WM/DE  Hyprland   发行版  Arch   主题  Nothing OS   难度  中
配色     ███ #000000 60%   ███ #0e0e0e 16%   ███ #f5f5f5 8%   ███ #d71921 4%
字体    Inter / JetBrainsMono Nerd Font
组件    bar: Quickshell 自绘 · shell: zsh + starship
一句话  黑白配色 + Glyph 点阵，Quickshell 负责栏和锁屏，适合想抄极简深色桌面的人。
亮点    • Glyph 矩阵用 cava 做圆形可视化；• 锁屏和 OSD 都由 Quickshell 绘制
标签    #quickshell #dark #minimal
```

## 安装

需要 Python 3.14+ 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
cp .env.example .env
$EDITOR .env                     # 填入 DEEPSEEK_API_KEY=sk-...（https://platform.deepseek.com/api_keys）
```

配置从项目根目录的 `.env` 读取（`python-dotenv`），`.env` 已被 `.gitignore` 忽略。
真实环境变量优先级高于 `.env`，所以 `DEEPSEEK_API_KEY=... uv run rice-ai digest`
或 systemd 的 `EnvironmentFile=` 依然可以覆盖文件里的值。

## 用法

```bash
# 抓取 + 总结 + 终端输出（默认本周 top 8）
uv run rice-ai digest
uv run rice-ai digest --window day --limit 5      # 今天的热帖，只处理 5 条
uv run rice-ai digest --force                     # 忽略去重/缓存，重新总结
uv run rice-ai digest --no-comments --no-dots     # 省钱模式：只靠正文 + 截图取色
uv run rice-ai digest --json > digest.json        # 机器可读，方便以后接推送/网页

# 只抓取入库，不调用 LLM（也用来检查 Reddit 是否在限流）
uv run rice-ai fetch --window week

# 从本地库重放卡片，不联网、不花钱
uv run rice-ai show
uv run rice-ai show 1xeuuwb
uv run rice-ai show --json
```

`--window` 可选 `hour day week month year all`（对应 Reddit 的 `top?t=`）。

## 工作流程

```
r/unixporn top/.rss
  └─ 解析 Post（id / 标题 / 作者 / 正文 / 缩略图 / 链接，WM 取自 [前缀]）
       ├─ SQLite 去重（同模型 30 天内总结过就跳过）
       ├─ 评论 RSS（按需 + 限次：正文够长且已有 dots 链接就跳过）
       ├─ 缩略图 → Pillow 量化取色板 + 深浅主题判断
       └─ 正文/OP 评论里的 GitHub 仓库 → raw README（缓存 7 天）
            └─ DeepSeek（JSON 模式）→ 中文卡片 → 终端渲染 / 入库
```

| 模块 | 职责 |
| --- | --- |
| `src/rice_ai/sources/reddit_rss.py` | 零认证 Atom feed 抓取与解析；`Source` 协议便于以后接 OAuth |
| `src/rice_ai/http.py` | 统一 UA、按 host 限速、429/5xx 退避、磁盘缓存（断网可用旧缓存） |
| `src/rice_ai/enrich/palette.py` | 缩略图取色（不用视觉模型） |
| `src/rice_ai/enrich/dots.py` | dots 仓库识别 + README 抓取清洗 |
| `src/rice_ai/llm.py` | DeepSeek 调用、提示词、JSON 校验/重试、失败降级 |
| `src/rice_ai/store.py` | SQLite：posts / comments / enrichment / cards / dots_repos |
| `src/rice_ai/render.py` | rich 终端卡片与 `--json` 输出 |
| `src/rice_ai/pipeline.py` | 编排 digest / fetch |

## 环境变量

全部可写在 `.env`（模板见 `.env.example`）或直接 export；**真实环境变量优先**。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | — | 必填（`digest` 需要，`fetch`/`show` 不需要） |
| `RICE_AI_MODEL` | `deepseek-chat` | 模型名，也是去重的 key |
| `RICE_AI_BASE_URL` | `https://api.deepseek.com` | 可指向任意 OpenAI 兼容网关 |
| `RICE_AI_SUBREDDIT` | `unixporn` | 目标板块 |
| `RICE_AI_DB` | `data/rice.db` | SQLite 路径 |
| `RICE_AI_CACHE` | `.cache` | HTTP / 图片缓存目录 |
| `RICE_AI_MIN_INTERVAL` | `6` | 访问 `reddit.com` 的最小间隔（秒） |
| `RICE_AI_LOG` | `WARNING` | `INFO`/`DEBUG` 可看到抓取细节 |
| `GITHUB_TOKEN` | — | 可选，提高 GitHub API 配额（无 token 走 raw 也能用） |

## 定时运行

```cron
# 每天早上 9 点生成一份日报（日志留在 data/）
0 9 * * * cd /path/to/rice-ai && uv run rice-ai digest --window day --limit 8 >> data/digest.log 2>&1
```

<details>
<summary>systemd timer 版本</summary>

`~/.config/systemd/user/rice-ai.service`
```ini
[Unit]
Description=rice-ai digest

[Service]
Type=oneshot
WorkingDirectory=%h/py-projs/rice-ai
Environment=DEEPSEEK_API_KEY=%h/.config/rice-ai.env
ExecStart=/usr/bin/env uv run rice-ai digest --window day --limit 8
StandardOutput=append:%h/py-projs/rice-ai/data/digest.log
StandardError=append:%h/py-projs/rice-ai/data/digest.log
```
（`Environment=` 不能直接读文件，请把 key 写进 `EnvironmentFile=%h/.config/rice-ai.env`。）

`~/.config/systemd/user/rice-ai.timer`
```ini
[Unit]
Description=Run rice-ai every morning

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true

[Install]
WantedBy=timers.target
```
```bash
systemctl --user enable --now rice-ai.timer
```
</details>

## 已知限制（都是有意接受的取舍）

- **只用 RSS，无 OAuth**：拿不到 score / flair / 投票比，每个 feed 最多 25 条；排序只能依赖 `top` feed 自身的顺序。
- **Reddit 匿名限流很紧**：实测 `reddit.com` 的 RSS 在 2-3 秒间隔下仍频繁 429，因此默认 6 秒间隔、评论最多抓 4 条帖、失败只重试 1 次。被限流时错误信息会提示等待 1 分钟。
- **没有视觉模型**：DeepSeek 是纯文本的，配色来自 Pillow 对 640px 缩略图的量化（近似值），布局/风格只能从文字推断。提示词禁止模型编造色板以外的颜色。
- **去重以模型名为 key**：换模型会重新总结已有帖子；`dedupe_days=30` 后会自动重跑。
- **dots 只抓 README**：不解析具体配置文件，够用来判断复刻难度和依赖。

## 开发

```bash
uv run pytest -q                      # 62 个测试，全部走 fixture + stub，不联网
uv run ruff check src tests           # CI 同样会跑
uv run rice-ai fetch --window week    # 冒烟：只验证 Reddit 通道
```

CI（`.github/workflows/ci.yml`）在每次 push/PR 到 `main` 时跑：`uv sync --locked --dev` →
`ruff check` → `pytest` → CLI 冒烟（含「无 key 必须非零退出」检查）。

测试夹具 `tests/fixtures/` 是真实抓取的 feed，改动解析逻辑时可直接回放。

## 后续方向

风格聚类成「方案」（如 Catppuccin + Hyprland 一族）、Telegram/ntfy 推送、Web 时间线、
Reddit OAuth 数据源、可选的 vision provider、本地 ollama 兜底。
