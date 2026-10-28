"""r/unixporn over the public Atom feeds (no OAuth, no API key).

Reddit's unauthenticated JSON endpoints are blocked (403), but the RSS feeds
still work: ``/r/<sub>/top/.rss?t=week`` lists 25 posts and
``/r/<sub>/comments/<id>/.rss`` returns the post body plus its comments.
Known gaps: no score, no flair, max 25 entries per feed, rate limited.
"""

from __future__ import annotations

import calendar
import logging
import re
from urllib.parse import urlparse

import feedparser
from bs4 import BeautifulSoup

from ..config import Config
from ..http import HttpClient
from ..models import Comment, Post, parse_wm_from_title

log = logging.getLogger(__name__)

BASE = "https://www.reddit.com"
_WS_RE = re.compile(r"[ \t\u00a0]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_COMMENT_TITLE_RE = re.compile(r"^/u/(?P<author>[^\s]+) on ", re.IGNORECASE)


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def _strip_user(value: str) -> str:
    return value.strip().lstrip("/").removeprefix("u/")


def _html_parts(html: str) -> tuple[str, str | None, list[str]]:
    """Split an entry's content HTML into (text, first image url, links)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Drop Reddit's boilerplate: "submitted by /u/x [link] [comments]"
    for table in soup.find_all("table"):
        for cell in table.find_all("td"):
            if cell.find("div", class_="md") is None and not cell.find("img"):
                cell.decompose()

    md = soup.find("div", class_="md")
    text = _clean_text((md or soup).get_text("\n"))

    image_url = None
    img = soup.find("img", src=True)
    if img is not None:
        image_url = img["src"]

    links = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("http://", "https://")) and href not in links:
            links.append(href)
    return text, image_url, links


def _entry_thumbnail(entry: object) -> str | None:
    media = getattr(entry, "media_thumbnail", None) or []
    if media and isinstance(media, list):
        url = media[0].get("url")
        if url:
            return str(url)
    return None


def _published_utc(entry: object) -> int:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return int(calendar.timegm(parsed))
    return 0


class RedditRss:
    name = "reddit-rss"

    def __init__(self, http: HttpClient, cfg: Config) -> None:
        self.http = http
        self.cfg = cfg

    # ------------------------------------------------------------- listings
    def listing_url(self, window: str) -> str:
        return f"{BASE}/r/{self.cfg.subreddit}/top/.rss?t={window}"

    def comments_url(self, post_id: str) -> str:
        return f"{BASE}/r/{self.cfg.subreddit}/comments/{post_id}/.rss?limit=50"

    def fetch_posts(self, window: str = "week", limit: int | None = None) -> list[Post]:
        url = self.listing_url(window)
        xml = self.http.get_text(url, ttl=900.0)
        posts = parse_posts_feed(xml, window=window)
        log.info("fetched %d posts from %s", len(posts), url)
        if limit is not None:
            posts = posts[:limit]
        return posts

    def fetch_comments(self, post: Post, limit: int = 12) -> list[Comment]:
        comments: list[Comment] = []
        try:
            # Comments are the most aggressively rate-limited endpoint: one retry max.
            xml = self.http.get_text(self.comments_url(post.id), ttl=900.0, retries=1)
        except Exception as exc:  # noqa: BLE001 - comments are optional enrichment
            log.warning("comments fetch failed for %s: %s", post.id, exc)
            return comments
        parsed, _ = parse_comments_feed(xml, post)
        return parsed[:limit]


def parse_posts_feed(xml: str, *, window: str = "week") -> list[Post]:
    feed = feedparser.parse(xml)
    if not feed.entries and not getattr(feed, "version", ""):
        raise ValueError(
            "not an Atom/RSS feed"
            + (f": {feed.bozo_exception}" if getattr(feed, "bozo", False) else "")
        )

    posts: list[Post] = []
    seen: set[str] = set()
    for entry in feed.entries:
        raw_id = str(getattr(entry, "id", "") or "")
        if not raw_id.startswith("t3_"):
            continue
        post_id = raw_id.removeprefix("t3_")
        if not post_id or post_id in seen:
            continue
        seen.add(post_id)

        html = ""
        contents = getattr(entry, "content", None) or []
        if contents:
            html = contents[0].get("value", "")
        if not html:
            html = getattr(entry, "summary", "") or ""

        text, img_from_html, links = _html_parts(html)
        title = _clean_text(str(getattr(entry, "title", "")))
        author = _strip_user(str(getattr(entry, "author", "")))
        permalink = str(getattr(entry, "link", ""))
        if not permalink:
            permalink = f"{BASE}/r/{'unixporn'}/comments/{post_id}/"

        posts.append(
            Post(
                id=post_id,
                title=title,
                author=author,
                created_utc=_published_utc(entry),
                permalink=permalink,
                selftext=text,
                image_url=_entry_thumbnail(entry) or img_from_html,
                links=links,
                wm=parse_wm_from_title(title),
                window=window,
            )
        )
    return posts


def parse_comments_feed(xml: str, post: Post) -> tuple[list[Comment], str]:
    """Return (comments, error). The feed's first entry repeats the post body."""
    try:
        feed = feedparser.parse(xml)
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - feedparser is defensive
        return [], str(exc)

    comments: list[Comment] = []
    for entry in feed.entries:
        raw_id = str(getattr(entry, "id", "") or "")
        if raw_id == f"t3_{post.id}":
            continue
        entry_title = _clean_text(str(getattr(entry, "title", "")))
        # The first entry is the post itself, titled exactly like the post.
        if entry_title == post.title:
            continue

        html = ""
        contents = getattr(entry, "content", None) or []
        if contents:
            html = contents[0].get("value", "")
        if not html:
            html = getattr(entry, "summary", "") or ""
        body, _, _ = _html_parts(html)
        if not body or body.lower().startswith("submitted by"):
            continue

        author = _strip_user(str(getattr(entry, "author", "")))
        match = _COMMENT_TITLE_RE.match(entry_title)
        if match:
            author = author or match.group("author")

        comment_id = raw_id.removeprefix("t1_") or f"{post.id}-{len(comments)}"
        comments.append(
            Comment(
                id=comment_id,
                post_id=post.id,
                author=author,
                body=body,
                ordinal=len(comments),
            )
        )
    return comments, ""


def is_supported_window(window: str) -> bool:
    return window in {"hour", "day", "week", "month", "year", "all"}


def reddit_post_id_from_url(url: str) -> str | None:
    """Extract the post id from a reddit permalink, if present."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "comments" in parts:
        idx = parts.index("comments")
        if len(parts) > idx + 1:
            return parts[idx + 1]
    return None
