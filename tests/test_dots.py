from __future__ import annotations

from rice_ai.enrich.dots import (
    clean_readme,
    extract_github_repos,
    fetch_readme,
    pick_dots_repo,
    readme_candidates,
)
from rice_ai.models import Comment, Post
from rice_ai.store import Store


def _post(*, selftext: str = "", author: str = "op") -> Post:
    return Post(
        id="abc123",
        title="[Hyprland] test rice",
        author=author,
        created_utc=0,
        permalink="/r/unixporn/comments/abc123/test/",
        selftext=selftext,
    )


def test_extracts_and_normalises_repo_urls():
    repos = extract_github_repos(
        "dots: https://github.com/Foo/Bar.git and https://github.com/foo/bar/tree/main"
    )
    assert repos == ["https://github.com/Foo/Bar"]


def test_ignores_non_repo_github_pages():
    text = (
        "https://github.com/topics/hyprland https://github.com/sponsors/someone "
        "https://github.com/login https://github.com/features/actions"
    )
    assert extract_github_repos(text) == []


def test_dedupes_case_insensitively():
    assert extract_github_repos(
        "https://github.com/A/B https://github.com/a/b", "https://github.com/A/B"
    ) == ["https://github.com/A/B"]


def test_ignores_trailing_punctuation():
    assert extract_github_repos("see https://github.com/o/r, thanks!") == [
        "https://github.com/o/r"
    ]


def test_pick_prefers_op_links():
    post = _post(selftext="dots https://github.com/op/self")
    comments = [
        Comment("c1", post.id, "op", "repo: https://github.com/op/comment", 0),
        Comment("c2", post.id, "fan", "https://github.com/fan/other", 1),
    ]
    assert pick_dots_repo(post, comments) == "https://github.com/op/self"


def test_pick_prefers_op_comments_over_others():
    post = _post(selftext="no links here")
    comments = [
        Comment("c1", post.id, "fan", "https://github.com/fan/other", 0),
        Comment("c2", post.id, "op", "https://github.com/op/comment", 1),
    ]
    assert pick_dots_repo(post, comments) == "https://github.com/op/comment"


def test_pick_returns_none_without_links():
    assert pick_dots_repo(_post(selftext="nothing"), []) is None


def test_clean_readme_strips_markup_and_images():
    raw = """<p align="center"><img src="logo.png"></p>

# Glyph
[![build](https://img.shields.io/badge/build-passing-green)](https://x)
![screenshot](shots/1.png)
Install with `./install.sh`, then read the wiki.
<br>
"""
    cleaned = clean_readme(raw)
    assert "shields.io" not in cleaned
    assert "screenshot" not in cleaned
    assert "<p" not in cleaned
    assert "# Glyph" in cleaned
    assert "install.sh" in cleaned


def test_clean_readme_truncates():
    assert len(clean_readme("x" * 9000)) <= 4000


def test_readme_candidates_order():
    candidates = readme_candidates("https://github.com/o/r")
    assert candidates[0].endswith("/HEAD/README.md")
    assert candidates[1].endswith("/HEAD/README.rst")
    assert candidates[2] == "https://api.github.com/repos/o/r/readme"
    assert readme_candidates("https://github.com") == []


def test_fetch_readme_uses_cache_and_negative_cache(cfg, stub_http, tmp_path):
    store = Store(tmp_path / "dots.db")
    excerpt = fetch_readme(stub_http, "https://github.com/o/r", store, cfg)
    assert excerpt and "Nothing OS rice" in excerpt
    calls_before = len(stub_http.calls)
    assert fetch_readme(stub_http, "https://github.com/o/r", store, cfg) == excerpt
    assert len(stub_http.calls) == calls_before  # served from sqlite cache
    store.close()


def test_fetch_readme_missing_repo_returns_none(cfg, stub_http_cls, tmp_path):
    http = stub_http_cls({"https://raw.githubusercontent.com/o/r/": "# hi"})
    store = Store(tmp_path / "dots.db")
    assert fetch_readme(http, "https://github.com/o/missing", store, cfg) is None
    assert store.get_dots_repo("https://github.com/o/missing", cfg.readme_ttl_days) == ""
    store.close()
