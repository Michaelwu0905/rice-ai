"""Find the dots (dotfiles) repository for a rice and fetch its README.

r/unixporn authors usually link a GitHub repo in the post body or in their
answers to the classic "dots?" comment. The README is the best textual proxy
for *how* the rice is built, which matters because the model cannot see images.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..config import Config
from ..http import HttpClient, HttpError
from ..models import Comment, Post
from ..store import Store

log = logging.getLogger(__name__)

README_LIMIT = 4000
README_TTL = 3600
GITHUB_API = "https://api.github.com/repos"
RESERVED_OWNERS = frozenset(
    {
        "about", "apps", "collections", "contact", "customer-stories", "enterprise",
        "events", "explore", "features", "join", "login", "marketplace", "new",
        "notifications", "orgs", "organizations", "pricing", "readme", "search",
        "security", "settings", "sponsors", "site", "topics", "trending", "users",
    }
)
RESERVED_REPOS = frozenset(
    {
        "issues", "pulls", "releases", "wiki", "actions", "commits", "raw",
        "archive", "blob", "tree", "graphs", "network", "projects", "pulse",
    }
)

REPO_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_BADGE_RE = re.compile(r"(shields\.io|badgen\.net|badge\.fury\.io|img\.shields)", re.IGNORECASE)


def extract_github_repos(*texts: str) -> list[str]:
    """Return canonical ``https://github.com/owner/repo`` URLs, order preserved."""
    repos: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in REPO_RE.finditer(text or ""):
            owner, repo = match.group(1), match.group(2)
            repo = repo.removesuffix(".git").rstrip(".,;:)]}\"'")
            if not owner or not repo:
                continue
            owner_l, repo_l = owner.lower(), repo.lower()
            if owner_l in RESERVED_OWNERS or repo_l in RESERVED_REPOS:
                continue
            if owner_l == repo_l and len(repo_l) <= 2:  # looks like a stray path
                continue
            url = f"https://github.com/{owner}/{repo}"
            if url.lower() in seen:
                continue
            seen.add(url.lower())
            repos.append(url)
    return repos


def pick_dots_repo(post: Post, comments: list[Comment]) -> str | None:
    """Prefer the OP's own links, then links posted by commenters."""
    own_comments = [c.body for c in comments if c.author and c.author == post.author]
    other_comments = [c.body for c in comments if c.author != post.author]
    for text in (post.selftext, *own_comments, *other_comments):
        repos = extract_github_repos(text)
        if repos:
            return repos[0]
    return None


def clean_readme(text: str, limit: int = README_LIMIT) -> str:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if _BADGE_RE.search(stripped):
            continue
        if stripped.startswith("<"):
            # keep visible text inside simple HTML blocks, drop the markup
            inner = BeautifulSoup(stripped, "html.parser").get_text(" ").strip()
            if inner:
                lines.append(inner)
            continue
        if stripped.startswith(("![", "[![")):
            continue  # screenshot / badge image line
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:limit].rstrip()


def readme_candidates(repo_url: str) -> list[str]:
    path = urlparse(repo_url).path.strip("/")
    if not path:
        return []
    return [
        f"https://raw.githubusercontent.com/{path}/HEAD/README.md",
        f"https://raw.githubusercontent.com/{path}/HEAD/README.rst",
        f"{GITHUB_API}/{path}/readme",
    ]


def _api_headers(cfg: Config) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github.raw"}
    if cfg.github_token:
        headers["Authorization"] = f"Bearer {cfg.github_token}"
    return headers


def fetch_readme(http: HttpClient, repo_url: str, store: Store, cfg: Config) -> str | None:
    cached = store.get_dots_repo(repo_url, cfg.readme_ttl_days)
    if cached is not None:
        return cached or None

    for candidate in readme_candidates(repo_url):
        headers = _api_headers(cfg) if candidate.startswith(GITHUB_API) else None
        try:
            text = http.get_text(candidate, ttl=README_TTL, headers=headers)
        except HttpError as exc:
            log.debug("readme miss %s: %s", candidate, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - never fail a post over dots
            log.warning("readme fetch error %s: %s", candidate, exc)
            continue
        excerpt = clean_readme(text)
        if not excerpt:
            continue
        store.save_dots_repo(repo_url, excerpt)
        return excerpt
    store.save_dots_repo(repo_url, "")  # negative cache, avoids retrying dead repos
    return None
