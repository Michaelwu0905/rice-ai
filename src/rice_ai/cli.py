"""Command line interface: digest / fetch / show."""

from __future__ import annotations

import argparse
import logging
import os
import time

from rich.console import Console
from rich.text import Text

from . import __version__
from .config import Config, load_env
from .http import HttpError
from .llm import LlmError
from .pipeline import DigestOptions, digest, fetch_posts
from .render import render_cards, render_header, render_json
from .sources import WINDOWS
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rice-ai",
        description="聚合 r/unixporn，用 DeepSeek 生成中文 rice 卡片并在终端展示。",
    )
    parser.add_argument("--version", action="version", version=f"rice-ai {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("digest", help="抓取 + 富化 + 总结，输出终端卡片")
    d.add_argument("--window", choices=WINDOWS, default="week", help="时间窗口（默认 week）")
    d.add_argument("--limit", type=int, default=None, help="最多处理几条（默认 8）")
    d.add_argument("--force", action="store_true", help="忽略去重与缓存，强制重新总结")
    d.add_argument("--no-dots", action="store_true", help="不抓取 dots 仓库 README")
    d.add_argument("--no-comments", action="store_true", help="不抓取评论")
    d.add_argument("--json", action="store_true", help="输出 JSON 而不是终端卡片")

    f = sub.add_parser("fetch", help="只抓取帖子入库，不调用 LLM")
    f.add_argument("--window", choices=WINDOWS, default="week")
    f.add_argument("--limit", type=int, default=None, help="最多抓几条（默认 feed 全部，最多 25）")

    s = sub.add_parser("show", help="从本地库重新渲染已生成的卡片（不联网）")
    s.add_argument("post_id", nargs="?", default=None, help="只看某一帖，例如 1xeuuwb")
    s.add_argument("--limit", type=int, default=10, help="显示最近多少张卡片（默认 10）")
    s.add_argument("--json", action="store_true")

    return parser


def _setup_logging() -> None:
    level = os.environ.get("RICE_AI_LOG", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )


def _cmd_digest(args: argparse.Namespace, cfg: Config) -> int:
    out = Console()
    err = Console(stderr=True)
    options = DigestOptions(
        window=args.window,
        limit=args.limit,
        force=args.force,
        with_dots=not args.no_dots,
        with_comments=not args.no_comments,
    )
    try:
        result = digest(cfg, options, status=err)
    except LlmError as exc:
        err.print(Text(f"✗ {exc}", style="bold red"))
        return 1
    except HttpError as exc:
        err.print(Text(f"✗ 网络请求失败：{exc}", style="bold red"))
        return 1
    except KeyboardInterrupt:
        err.print(Text("已中断", style="yellow"))
        return 130

    if args.json:
        render_json(result.rows, console=out)
    else:
        render_header(len(result.rows), window=args.window, model=cfg.model, console=out)
        render_cards(result.rows, console=out)

    footer = Text()
    footer.append(f"抓取 {result.fetched} 条", style="dim")
    if result.skipped:
        footer.append(f" · 跳过已总结 {result.skipped} 条", style="dim")
    if result.llm_calls:
        tokens = f" / {result.tokens / 1000:.1f}k tokens" if result.tokens else ""
        footer.append(f" · LLM 调用 {result.llm_calls} 次{tokens}", style="dim")
    if result.failures:
        footer.append(f" · 失败 {len(result.failures)} 条", style="yellow")
    err.print(footer)
    for post_id, reason in result.failures:
        err.print(Text(f"  ! {post_id}: {reason}", style="yellow"))
    return 0


def _cmd_fetch(args: argparse.Namespace, cfg: Config) -> int:
    out = Console()
    err = Console(stderr=True)
    try:
        posts = fetch_posts(cfg, window=args.window, limit=args.limit)
    except HttpError as exc:
        err.print(Text(f"✗ 网络请求失败：{exc}", style="bold red"))
        return 1
    except KeyboardInterrupt:
        err.print(Text("已中断", style="yellow"))
        return 130

    for index, post in enumerate(posts, start=1):
        date = time.strftime("%Y-%m-%d", time.localtime(post.created_utc)) if post.created_utc else "—"
        line = Text(f"{index:>2}. ")
        line.append(f"{date} ", style="dim")
        line.append(post.title)
        line.append(f"  {post.id}", style="dim")
        out.print(line)
    with Store(cfg.db_path) as store:
        total = store.count_posts()
    out.print(Text(f"共 {len(posts)} 条 · 库存 {total} 条 · {cfg.db_path}", style="dim"))
    return 0


def _cmd_show(args: argparse.Namespace, cfg: Config) -> int:
    out = Console()
    err = Console(stderr=True)
    with Store(cfg.db_path) as store:
        if args.post_id:
            row = store.get_card_row(args.post_id)
            rows = [row] if row else []
        else:
            rows = store.list_cards(args.limit)

    if not rows:
        err.print(Text("本地还没有卡片。先运行 `rice-ai digest`。", style="yellow"))
        return 1

    if args.json:
        render_json(rows, console=out)
    else:
        render_header(len(rows), window="本地库", model=rows[0].card.model, console=out)
        render_cards(rows, console=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env()  # .env wins nothing over real env vars, but fills in the key
    cfg = Config.from_env()
    handlers = {"digest": _cmd_digest, "fetch": _cmd_fetch, "show": _cmd_show}
    return handlers[args.command](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
