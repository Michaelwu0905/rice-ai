"""Pipeline orchestration: fetch -> dedupe -> enrich -> summarize -> store."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.text import Text

from .config import Config
from .enrich.dots import extract_github_repos, fetch_readme, pick_dots_repo
from .enrich.palette import fetch_palette
from .http import HttpClient
from .llm import MISSING_KEY_MESSAGE, DeepSeekClient, LlmError, fallback_card
from .models import Comment, Palette, Post, RiceCard
from .sources.reddit_rss import RedditRss
from .store import CardRow, Enrichment, Store

log = logging.getLogger(__name__)


def _is_fatal_llm_error(exc: LlmError) -> bool:
    """Auth failures hit every post; abort instead of writing junk cards."""
    text = str(exc).lower()
    return "401" in text or "403" in text or "authentication" in text or "api key" in text


@dataclass(slots=True)
class DigestOptions:
    window: str = "week"
    limit: int | None = None
    force: bool = False
    with_dots: bool = True
    with_comments: bool = True
    max_comment_fetches: int | None = None


@dataclass(slots=True)
class DigestResult:
    rows: list[CardRow] = field(default_factory=list)
    fetched: int = 0
    skipped: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0


def fetch_posts(
    cfg: Config,
    *,
    window: str,
    limit: int | None = None,
    http: HttpClient | None = None,
    store: Store | None = None,
    source: RedditRss | None = None,
) -> list[Post]:
    own_http = http is None
    own_store = store is None
    http = http or HttpClient(cfg)
    store = store or Store(cfg.db_path)
    source = source or RedditRss(http, cfg)
    try:
        posts = source.fetch_posts(window, limit)
        for post in posts:
            store.upsert_post(post)
        return posts
    finally:
        if own_http:
            http.close()
        if own_store:
            store.close()


def _needs_comments(post: Post) -> bool:
    """Comments are expensive (429-prone) and mostly add dots links / fonts.

    Skip them when the post already has a long body with a repository link.
    """
    return not (len(post.selftext) >= 200 and extract_github_repos(post.selftext))


def _gather_comments(
    source: RedditRss,
    store: Store,
    post: Post,
    cfg: Config,
    *,
    enabled: bool,
    budget: int,
) -> tuple[list[Comment], bool]:
    """Return (comments, fetched_from_network)."""
    comments = store.get_comments(post.id, cfg.max_comments)
    if comments or not enabled or budget <= 0 or not _needs_comments(post):
        return comments, False
    comments = source.fetch_comments(post, cfg.max_comments)
    store.upsert_comments(comments)
    return comments, True


def _enrich(
    http: HttpClient,
    store: Store,
    post: Post,
    comments: list[Comment],
    cfg: Config,
    *,
    with_dots: bool,
    force: bool,
) -> tuple[Palette, str | None, str | None]:
    cached = store.get_enrichment(post.id) if not force else None
    if cached is not None:
        return cached.palette, cached.dots_url, cached.readme_excerpt

    palette = fetch_palette(http, post)
    dots_url = pick_dots_repo(post, comments) if with_dots else None
    readme = fetch_readme(http, dots_url, store, cfg) if dots_url else None
    store.save_enrichment(post.id, palette, dots_url, readme)
    return palette, dots_url, readme


def digest(
    cfg: Config,
    options: DigestOptions | None = None,
    *,
    http: HttpClient | None = None,
    store: Store | None = None,
    source: RedditRss | None = None,
    llm: Any | None = None,
    status: Console | None = None,
) -> DigestResult:
    options = options or DigestOptions()
    status = status or Console(stderr=True)
    result = DigestResult()

    if not cfg.api_key:
        raise LlmError(MISSING_KEY_MESSAGE)

    cfg.ensure_dirs()
    own_http = http is None
    own_store = store is None
    own_llm = llm is None
    http = http or HttpClient(cfg)
    store = store or Store(cfg.db_path)
    source = source or RedditRss(http, cfg)
    llm = llm or DeepSeekClient(cfg)

    try:
        limit = options.limit if options.limit is not None else cfg.default_limit
        posts = source.fetch_posts(options.window, limit)
        result.fetched = len(posts)
        for post in posts:
            store.upsert_post(post)
        status.print(Text(f"抓到 {len(posts)} 条 r/{cfg.subreddit} · {options.window}", style="dim"))

        comment_budget = (
            options.max_comment_fetches
            if options.max_comment_fetches is not None
            else cfg.max_comment_fetches
        )
        for index, post in enumerate(posts, start=1):
            prefix = Text(f"[{index}/{len(posts)}] ", style="dim")
            if not options.force and store.has_recent_card(post.id, cfg.model, cfg.dedupe_days):
                result.skipped += 1
                status.print(prefix + Text(f"跳过（已总结）{post.title}", style="dim"))
                continue

            palette = Palette()
            dots_url: str | None = None
            try:
                comments, fetched = _gather_comments(
                    source,
                    store,
                    post,
                    cfg,
                    enabled=options.with_comments,
                    budget=comment_budget,
                )
                if fetched:
                    comment_budget -= 1
                palette, dots_url, readme = _enrich(
                    http,
                    store,
                    post,
                    comments,
                    cfg,
                    with_dots=options.with_dots,
                    force=options.force,
                )
                status.print(prefix + Text(f"总结 {post.title}", style="cyan"))
                llm_result = llm.summarize(post, comments, palette, readme, dots_url)
                card: RiceCard = llm_result.card
                result.tokens += int((getattr(llm_result, "usage", None) or {}).get("total_tokens", 0))
            except LlmError as exc:
                if _is_fatal_llm_error(exc):
                    raise
                log.warning("LLM failed for %s: %s", post.id, exc)
                result.failures.append((post.id, str(exc)))
                card = fallback_card(post, palette, dots_url, cfg.model, reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - one bad post must not kill the run
                log.warning("post %s failed: %s", post.id, exc)
                result.failures.append((post.id, f"{type(exc).__name__}: {exc}"))
                continue

            if not card.model:
                card.model = cfg.model
            store.save_card(card)
            result.rows.append(
                CardRow(
                    post=post,
                    card=card,
                    enrichment=store.get_enrichment(post.id)
                    or Enrichment(post.id, Palette(), None, None, 0),
                    created_at=int(time.time()),
                )
            )

        result.llm_calls = int(getattr(llm, "call_count", 0))
        return result
    finally:
        if own_http:
            http.close()
        if own_store:
            store.close()
        if own_llm and hasattr(llm, "close"):
            llm.close()

