"""Terminal rendering with rich: colour swatches, metadata grid, clickable links."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .models import RiceCard
from .store import CardRow

DIFFICULTY_LABELS = {
    "easy": ("低", "green"),
    "medium": ("中", "yellow"),
    "hard": ("高", "red"),
}


def _fmt_date(created_utc: int) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.localtime(created_utc)) if created_utc else "—"


def _dots(row: CardRow, card: RiceCard) -> str | None:
    return card.dots_url or row.enrichment.dots_url


def cards_payload(rows: list[CardRow]) -> list[dict[str, Any]]:
    """Machine-friendly output for future push channels / web UI."""
    payload = []
    for row in rows:
        data = row.card.as_dict()
        data.update(
            {
                "url": row.post.url,
                "author": row.post.author,
                "created_utc": row.post.created_utc,
                "image_url": row.post.image_url,
                "dots_url": _dots(row, row.card),
                "palette": row.enrichment.palette.as_dict(),
                "original_title": row.post.title,
            }
        )
        payload.append(data)
    return payload


def render_json(rows: list[CardRow], console: Console | None = None) -> None:
    console = console or Console()
    console.print_json(json.dumps(cards_payload(rows), ensure_ascii=False))


def _swatch(hex_value: str) -> Text:
    block = Text("   ")
    block.stylize(f"on {hex_value}")
    return block


def _palette_text(row: CardRow, card: RiceCard) -> Text:
    palette = row.enrichment.palette
    if not palette.colors:
        return Text("—", style="dim")
    text = Text()
    colors = palette.colors
    if card.colors:
        wanted = {c.lower() for c in card.colors}
        colors = [c for c in colors if c.hex.lower() in wanted] or colors
    for index, color in enumerate(colors):
        if index:
            text.append("  ")
        text.append_text(_swatch(color.hex))
        text.append(f" {color.hex} {round(color.share * 100)}%", style="dim")
    text.append(f"  (背景 {palette.bg_hex}，{'深色' if palette.dark else '浅色'})", style="dim")
    return text


def _field_grid(row: CardRow, card: RiceCard) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()

    wm = card.wm or "—"
    distro = card.distro or "—"
    theme = card.theme or "—"
    label, style = DIFFICULTY_LABELS.get(card.difficulty, ("中", "yellow"))
    difficulty = Text(label, style=style)

    head = Table.grid(padding=(0, 2))
    head.add_column(style="dim", no_wrap=True)
    head.add_column(overflow="fold")
    head.add_column(style="dim", no_wrap=True)
    head.add_column(overflow="fold")
    head.add_row("WM/DE", wm, "发行版", distro)
    head.add_row("主题", theme, "难度", difficulty)
    grid.add_row("概况", head)
    grid.add_row("配色", _palette_text(row, card))
    if card.fonts:
        grid.add_row("字体", Text(" / ".join(card.fonts)))
    if card.components:
        grid.add_row(
            "组件",
            Text(" · ".join(f"{c.name}: {c.detail}" for c in card.components), overflow="fold"),
        )
    grid.add_row("一句话", Text(card.summary_zh or "—", overflow="fold"))
    if card.highlights:
        grid.add_row("亮点", Text("；".join(f"• {h}" for h in card.highlights), overflow="fold"))
    if card.tags:
        grid.add_row("标签", Text(" ".join(f"#{t}" for t in card.tags), style="cyan", overflow="fold"))
    return grid


def _source_line(row: CardRow, card: RiceCard) -> Text:
    line = Text()
    line.append("原作 ", style="dim")
    line.append(row.post.author or "—", style="magenta")
    line.append(f" · {_fmt_date(row.post.created_utc)} · ", style="dim")
    line.append("原帖", style=f"link {row.post.url} cyan")
    if dots := _dots(row, card):
        line.append("   dots ", style="dim")
        short = dots.removeprefix("https://").removeprefix("http://").removeprefix("www.")
        line.append(short, style=f"link {dots} cyan")
    return line


def render_cards(rows: list[CardRow], console: Console | None = None) -> None:
    console = console or Console(highlight=False)
    for index, row in enumerate(rows, start=1):
        card = row.card
        title = f"{index}. {card.title_zh or row.post.title}"
        console.print(Rule(Text(title, style="bold cyan"), style="cyan", align="left"))
        if card.llm_error:
            console.print(Text("⚠ LLM 总结失败，以下为原文摘要", style="yellow"))
        console.print(_source_line(row, card))
        console.print(_field_grid(row, card))
        console.print()
    if not rows:
        console.print(Text("没有新的卡片可展示。试试 `rice-ai digest --force`。", style="yellow"))


def render_header(count: int, *, window: str, model: str, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        Text.assemble(
            ("rice-ai", "bold green"),
            (f" · r/unixporn · {window} · ", "dim"),
            (f"{count} 套方案", "bold"),
            (f" · {model}", "dim"),
        )
    )
