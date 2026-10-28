from __future__ import annotations

from pathlib import Path

import pytest

from rice_ai.config import Config
from rice_ai.http import HttpError
from rice_ai.models import Comment, Component, Post, RiceCard
from rice_ai.sources.reddit_rss import parse_comments_feed, parse_posts_feed

FIXTURES = Path(__file__).parent / "fixtures"
TOP_XML = (FIXTURES / "unixporn_top_week.rss").read_text("utf-8")
COMMENTS_XML = (FIXTURES / "unixporn_comments_1xeuuwb.rss").read_text("utf-8")
THUMB = (FIXTURES / "thumb.png").read_bytes()
README_MD = "# Glyph\n\nA Nothing OS rice for Hyprland + Quickshell.\n\n```bash\n./install.sh\n```\n"


@pytest.fixture()
def top_xml() -> str:
    return TOP_XML


@pytest.fixture()
def comments_xml() -> str:
    return COMMENTS_XML


@pytest.fixture()
def thumb_bytes() -> bytes:
    return THUMB


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    return Config(
        api_key="test-key",
        db_path=tmp_path / "rice.db",
        cache_dir=tmp_path / "cache",
        min_interval=0.0,
        other_interval=0.0,
    )


class StubHttp:
    """Minimal HttpClient stand-in: prefix-routed responses, no network."""

    def __init__(self, routes: dict[str, bytes | str]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, ttl=900.0, headers=None, retries=None) -> bytes:
        self.calls.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                return body if isinstance(body, bytes) else body.encode("utf-8")
        raise HttpError(f"no stub route for {url}")

    def get_text(self, url: str, *, ttl=900.0, headers=None, retries=None) -> str:
        return self.get(url, ttl=ttl, headers=headers, retries=retries).decode("utf-8")


class StubSource:
    name = "stub"

    def __init__(self, posts: list[Post], comments: dict[str, list[Comment]]) -> None:
        self.posts = posts
        self.comments = comments
        self.listing_fetches = 0
        self.comment_fetches = 0

    def fetch_posts(self, window: str = "week", limit: int | None = None) -> list[Post]:
        self.listing_fetches += 1
        return self.posts[:limit] if limit else list(self.posts)

    def fetch_comments(self, post: Post, limit: int = 12) -> list[Comment]:
        self.comment_fetches += 1
        return self.comments.get(post.id, [])[:limit]


class StubLlm:
    def __init__(self, *, fail_for: set[str] | None = None, model: str = "deepseek-chat") -> None:
        self.call_count = 0
        self.fail_for = fail_for or set()
        self.model = model

    def summarize(self, post, comments, palette, readme, dots_url):
        from rice_ai.llm import LlmError

        self.call_count += 1
        if post.id in self.fail_for:
            raise LlmError("stub failure")
        card = RiceCard(
            post_id=post.id,
            title_zh=f"中文标题 {post.id}",
            summary_zh="摘要" * 10,
            wm=post.wm,
            colors=palette.hexes[:3],
            dots_url=dots_url,
            highlights=[f"{len(comments)} 条评论"],
            tags=["stub"],
            model=self.model,
            components=[Component("bar", "stub-bar")],
        )

        class _Result:
            pass

        result = _Result()
        result.card = card
        return result

    def close(self) -> None:
        pass


@pytest.fixture()
def posts() -> list[Post]:
    return parse_posts_feed(TOP_XML, window="week")


@pytest.fixture()
def comments(posts: list[Post]) -> dict[str, list[Comment]]:
    parsed, _ = parse_comments_feed(COMMENTS_XML, posts[0])
    return {posts[0].id: parsed}


@pytest.fixture()
def stub_http_cls():
    return StubHttp


@pytest.fixture()
def stub_http() -> StubHttp:
    return StubHttp(
        {
            "https://external-preview.redd.it/": THUMB,
            "https://preview.redd.it/": THUMB,
            "https://raw.githubusercontent.com/": README_MD,
        }
    )


@pytest.fixture()
def stub_llm() -> StubLlm:
    return StubLlm()


@pytest.fixture()
def stub_llm_cls():
    return StubLlm


@pytest.fixture()
def stub_source(posts: list[Post], comments: dict[str, list[Comment]]) -> StubSource:
    return StubSource(posts, comments)
