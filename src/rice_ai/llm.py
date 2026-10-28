"""DeepSeek summarization: post + comments + palette + dots README -> RiceCard.

The model is text-only, so the prompt is explicit about provenance: colours may
only come from the extracted palette, everything unknown must be null, and the
output has to be a single JSON object (``response_format=json_object``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Self

import httpx

from .config import Config
from .models import DIFFICULTIES, Comment, Component, Palette, Post, RiceCard

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_INPUT_SELFTEXT = 3000
MAX_INPUT_COMMENT = 600
MAX_INPUT_COMMENTS = 8

SYSTEM_PROMPT = """你是 Linux ricing 编辑，为中文读者总结 r/unixporn 的桌面配置（rice）方案。
你只负责整理材料中确实存在的信息，不猜测、不美化。

规则：
1. 只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块。
2. 所有描述用简体中文；专有名词（窗口管理器、主题、字体名）保留原文。
2b. wm / distro / theme 只填名称（wm 如 Hyprland，distro 如 Arch Linux，theme 如
   Catppuccin Mocha 或 Frutiger Aero），不要在 theme 里写功能说明，三者各自不超过 20 字。
3. colors 只能原样复制「截图主色板」里给出的 hex，禁止自己编造或调整。
4. 材料里没提到的字段填 null（字符串）或空数组（列表），不要用「未知」之类的占位词。
5. summary_zh 不超过 120 字，讲清楚：什么桌面环境、什么风格、最值得借鉴的点。
5b. title_zh 不要带原始帖子的 [WM] 前缀，直接写中文标题。
6. highlights 最多 3 条，每条不超过 40 字，必须是具体的配置事实（不是空泛评价）。
7. difficulty 依据 dots 仓库是否完整、依赖多少判断：easy / medium / hard。
8. worth_reproducing 表示普通用户是否值得照着复刻。"""

SCHEMA_HINT = """输出 JSON 字段：
{
  "title_zh": "中文标题（不超过 30 字）",
  "summary_zh": "中文摘要（不超过 120 字）",
  "wm": "窗口管理器/桌面环境，如 Hyprland、KDE Plasma、bspwm",
  "distro": "发行版，如 Arch、NixOS、Ubuntu",
  "theme": "GTK/整体主题名，如 Catppuccin Mocha",
  "colors": ["#rrggbb"],
  "fonts": ["字体名"],
  "components": [{"name": "bar", "detail": "Waybar + 自绘模块"}],
  "dots_url": "https://github.com/owner/repo 或 null",
  "difficulty": "easy|medium|hard",
  "highlights": ["具体亮点"],
  "tags": ["catppuccin", "tiling"],
  "worth_reproducing": true
}"""


MISSING_KEY_MESSAGE = (
    "DEEPSEEK_API_KEY 未设置。请在项目根目录的 .env 里填写 "
    "`DEEPSEEK_API_KEY=sk-...`（可先 `cp .env.example .env`），"
    "或 export 该变量；也可以用 `rice-ai fetch` 只抓取不总结。"
)


class LlmError(RuntimeError):
    pass


@dataclass(slots=True)
class LlmResult:
    card: RiceCard
    usage: dict[str, Any]
    calls: int


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def build_messages(
    post: Post,
    comments: list[Comment],
    palette: Palette,
    readme: str | None,
    dots_url: str | None,
) -> list[dict[str, str]]:
    selftext = _truncate(post.selftext, MAX_INPUT_SELFTEXT) or "（无正文）"
    comment_lines = []
    for comment in comments[:MAX_INPUT_COMMENTS]:
        body = _truncate(comment.body, MAX_INPUT_COMMENT)
        if body:
            comment_lines.append(f"- u/{comment.author}: {body}")
    comments_block = "\n".join(comment_lines) or "（无评论）"

    if palette.colors:
        swatches = "、".join(
            f"{c.hex} ({round(c.share * 100)}%)" for c in palette.colors
        )
        palette_block = f"截图主色板（按占比排序）：{swatches}\n背景色：{palette.bg_hex}，整体{'深色' if palette.dark else '浅色'}主题"
    else:
        palette_block = "截图主色板：无（未能取色，colors 请返回空数组）"

    readme_block = _truncate(readme, 4000) if readme else "（未找到 dots 仓库说明）"

    user = f"""请总结下面这套 rice。

【帖子标题】{post.title}
【作者】u/{post.author}
【标题中的 WM/DE 前缀】{post.wm or "无"}
【正文】
{selftext}

【热门评论】
{comments_block}

【{palette_block}】

【dots 仓库】{dots_url or "无"}
【dots README 摘录】
{readme_block}

{SCHEMA_HINT}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_card_json(
    raw: str,
    *,
    post: Post,
    palette: Palette,
    dots_url: str | None,
    model: str,
) -> RiceCard:
    """Parse and clamp the model output into a trustworthy RiceCard."""
    data = json.loads(_strip_code_fence(raw))
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")  # noqa: TRY004

    allowed_colors = {c.lower() for c in palette.hexes}
    colors: list[str] = []
    for value in data.get("colors") or []:
        if not isinstance(value, str):
            continue
        candidate = value.strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", candidate):
            continue
        if allowed_colors and candidate not in allowed_colors:
            continue
        if candidate not in colors:
            colors.append(candidate)
    if not colors:
        colors = palette.hexes[:5]

    components = []
    for item in data.get("components") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if name:
            components.append((name[:24], detail[:60]))
    components = components[:5]

    highlights = [
        str(h).strip()[:80]
        for h in (data.get("highlights") or [])
        if isinstance(h, (str, int, float)) and str(h).strip()
    ][:3]
    tags = [
        str(t).strip().lower()[:24]
        for t in (data.get("tags") or [])
        if isinstance(t, (str, int, float)) and str(t).strip()
    ][:6]
    fonts = [str(f).strip()[:60] for f in (data.get("fonts") or []) if str(f).strip()][:4]

    difficulty = str(data.get("difficulty") or "medium").strip().lower()
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"

    def _opt(key: str, limit: int = 60) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "unknown", "未知", "n/a"}:
            return None
        return text[:limit]

    card = RiceCard(
        post_id=post.id,
        title_zh=(_opt("title_zh") or post.title)[:60],
        summary_zh=_opt("summary_zh", 200) or _truncate(post.selftext, 120) or post.title,
        wm=_opt("wm", 30) or post.wm,
        distro=_opt("distro", 40),
        theme=_opt("theme", 30),
        colors=colors,
        fonts=fonts,
        components=[Component(name=n, detail=d) for n, d in components],
        dots_url=dots_url or _opt("dots_url"),
        difficulty=difficulty,
        highlights=highlights,
        tags=tags,
        worth_reproducing=bool(data.get("worth_reproducing", True)),
        llm_error=False,
        model=model,
    )
    return card


def fallback_card(
    post: Post,
    palette: Palette,
    dots_url: str | None,
    model: str,
    *,
    reason: str,
) -> RiceCard:
    summary = _truncate(post.selftext, 160) if post.selftext else "（无正文，LLM 总结失败）"
    return RiceCard(
        post_id=post.id,
        title_zh=post.title[:60],
        summary_zh=f"{summary}",
        wm=post.wm,
        colors=palette.hexes[:5],
        dots_url=dots_url,
        difficulty="medium",
        highlights=[],
        tags=[],
        worth_reproducing=True,
        llm_error=True,
        model=model,
    )


class DeepSeekClient:
    def __init__(
        self,
        cfg: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.cfg = cfg
        self.sleep = sleep
        self.call_count = 0
        self._client = httpx.Client(
            timeout=httpx.Timeout(cfg.timeout, connect=20.0),
            transport=transport,
            headers={"User-Agent": cfg.user_agent},
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.api_key:
            raise LlmError(MISSING_KEY_MESSAGE)
        url = f"{self.cfg.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        last_error = ""
        for attempt, backoff in enumerate((0.0, 3.0, 9.0)):
            if backoff:
                self.sleep(backoff)
            try:
                self.call_count += 1
                response = self._client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("LLM request failed (%d/3): %s", attempt + 1, last_error)
                continue
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                log.warning("LLM retryable status %s (%d/3)", response.status_code, attempt + 1)
                continue
            if response.status_code >= 400:
                raise LlmError(f"DeepSeek HTTP {response.status_code}: {response.text[:300]}")
            try:
                return response.json()
            except ValueError as exc:
                raise LlmError(f"DeepSeek returned non-JSON response: {exc}") from exc
        raise LlmError(f"DeepSeek request failed after retries: {last_error}")

    def summarize(
        self,
        post: Post,
        comments: list[Comment],
        palette: Palette,
        readme: str | None,
        dots_url: str | None,
    ) -> LlmResult:
        model = self.cfg.model
        messages = build_messages(post, comments, palette, readme, dots_url)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        }
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected DeepSeek payload: {data}") from exc

        try:
            card = parse_card_json(
                content, post=post, palette=palette, dots_url=dots_url, model=model
            )
        except (ValueError, TypeError) as exc:
            log.warning("invalid JSON for %s (%s), asking once more", post.id, exc)
            retry_messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": "上面的输出无法解析。请只输出符合字段要求的单个 JSON 对象，不要任何多余文字。"},
            ]
            data = self._post({**payload, "messages": retry_messages, "temperature": 0.1})
            try:
                content = data["choices"][0]["message"]["content"]
                card = parse_card_json(
                    content, post=post, palette=palette, dots_url=dots_url, model=model
                )
            except (ValueError, TypeError, KeyError, IndexError) as retry_exc:
                raise LlmError(f"model output is not valid JSON after retry: {retry_exc}") from retry_exc

        return LlmResult(card=card, usage=data.get("usage") or {}, calls=self.call_count)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
