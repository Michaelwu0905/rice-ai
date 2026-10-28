from __future__ import annotations

import json

import httpx
import pytest

from rice_ai.llm import (
    DeepSeekClient,
    LlmError,
    build_messages,
    fallback_card,
    parse_card_json,
)
from rice_ai.models import Comment, Palette, PaletteColor

PALETTE = Palette(
    colors=[PaletteColor("#000000", 0.6), PaletteColor("#d71921", 0.04)],
    dark=True,
    bg_hex="#000000",
)

GOOD_CARD = {
    "title_zh": "Nothing OS 风 Hyprland",
    "summary_zh": "Hyprland + Quickshell 复刻 Nothing OS 的点阵风格。",
    "wm": "Hyprland",
    "distro": "Arch",
    "theme": None,
    "colors": ["#000000", "#ff00ff", "#d71921"],
    "fonts": ["Inter"],
    "components": [{"name": "bar", "detail": "Quickshell 自绘"}],
    "dots_url": "https://github.com/x/y",
    "difficulty": "hard",
    "highlights": ["Glyph 矩阵用 cava", "锁屏由 Quickshell 绘制", "第三条", "第四条"],
    "tags": ["quickshell", "dark"],
    "worth_reproducing": True,
}


def test_parse_filters_colours_outside_the_palette(posts):
    card = parse_card_json(
        json.dumps(GOOD_CARD), post=posts[0], palette=PALETTE, dots_url=None, model="m"
    )
    assert card.colors == ["#000000", "#d71921"]  # #ff00ff was invented


def test_parse_clamps_lists_and_prefers_extracted_dots(posts):
    card = parse_card_json(
        json.dumps(GOOD_CARD),
        post=posts[0],
        palette=PALETTE,
        dots_url="https://github.com/real/dots",
        model="m",
    )
    assert card.dots_url == "https://github.com/real/dots"
    assert len(card.highlights) == 3
    assert card.difficulty == "hard"
    assert card.components[0].name == "bar"


def test_parse_handles_code_fences_and_unknown_difficulty(posts):
    raw = "```json\n" + json.dumps({**GOOD_CARD, "difficulty": "中等"}) + "\n```"
    card = parse_card_json(raw, post=posts[0], palette=PALETTE, dots_url=None, model="m")
    assert card.difficulty == "medium"


def test_parse_falls_back_to_post_fields(posts):
    card = parse_card_json(
        json.dumps({"summary_zh": "只有摘要"}), post=posts[0], palette=PALETTE, dots_url=None, model="m"
    )
    assert card.title_zh == posts[0].title
    assert card.wm == "Hyprland"
    assert card.colors == PALETTE.hexes
    assert card.theme is None


def test_parse_normalises_nullish_strings(posts):
    payload = {**GOOD_CARD, "theme": "未知", "distro": "null", "wm": "  "}
    card = parse_card_json(json.dumps(payload), post=posts[0], palette=PALETTE, dots_url=None, model="m")
    assert card.theme is None
    assert card.distro is None
    assert card.wm == "Hyprland"  # from the title prefix


def test_parse_rejects_non_object(posts):
    with pytest.raises(ValueError):
        parse_card_json("[1, 2]", post=posts[0], palette=PALETTE, dots_url=None, model="m")


def _client(handler, cfg, sleeps=None):
    return DeepSeekClient(
        cfg, transport=httpx.MockTransport(handler), sleep=(sleeps.append if sleeps is not None else (lambda _s: None))
    )


def test_retry_after_invalid_json(cfg, posts):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        content = "not json at all" if len(calls) == 1 else json.dumps(GOOD_CARD)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 10}}
        )

    with _client(handler, cfg) as client:
        result = client.summarize(posts[0], [], PALETTE, "readme", "https://github.com/x/y")
    assert client.call_count == 2
    assert result.card.title_zh == GOOD_CARD["title_zh"]
    assert calls[1]["messages"][-1]["role"] == "user"


def test_http_error_raises_llm_error(cfg, posts):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with _client(handler, cfg) as client, pytest.raises(LlmError, match="400"):
        client.summarize(posts[0], [], PALETTE, None, None)


def test_missing_api_key_is_reported_clearly(posts):
    from rice_ai.config import Config

    client = DeepSeekClient(Config(api_key=None))
    try:
        with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
            client.summarize(posts[0], [], PALETTE, None, None)
    finally:
        client.close()


def test_prompt_contains_palette_and_constraints(posts):
    messages = build_messages(
        posts[0],
        [Comment("c1", posts[0].id, "someone", "what font is that?", 0)],
        PALETTE,
        "README body",
        "https://github.com/x/y",
    )
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "#d71921" in user
    assert "what font is that?" in user
    assert "https://github.com/x/y" in user
    assert "README body" in user
    assert "只输出一个 JSON" in messages[0]["content"]


def test_fallback_card_marks_the_error(posts):
    card = fallback_card(posts[0], PALETTE, "https://github.com/x/y", "m", reason="boom")
    assert card.llm_error is True
    assert card.post_id == posts[0].id
    assert card.colors == PALETTE.hexes
    assert card.dots_url == "https://github.com/x/y"


def test_long_summary_is_not_clipped(posts):
    payload = {**GOOD_CARD, "summary_zh": "这" * 150, "title_zh": "标" * 80}
    card = parse_card_json(json.dumps(payload), post=posts[0], palette=PALETTE, dots_url=None, model="m")
    assert len(card.summary_zh) == 150  # 120 chars is the prompt's limit, not a hard cut
    assert len(card.title_zh) == 60
