from __future__ import annotations

import pytest

from rice_ai.models import parse_wm_from_title
from rice_ai.sources.reddit_rss import (
    is_supported_window,
    parse_comments_feed,
    parse_posts_feed,
    reddit_post_id_from_url,
)


def test_parse_top_week_feed(posts):
    assert len(posts) == 25
    first = posts[0]
    assert first.id == "1xeuuwb"
    assert first.wm == "Hyprland"
    assert first.author == "Twidlex-"
    assert first.created_utc == 1792909316
    assert first.image_url and first.image_url.startswith("https://external-preview.redd.it/")
    assert "Quickshell" in first.selftext
    assert "https://github.com/Nebelung-cloud/glyph-rice" in first.links
    assert first.permalink.startswith("https://www.reddit.com/r/unixporn/comments/")
    assert first.window == "week"


def test_post_ids_are_unique_and_bare(posts):
    ids = [p.id for p in posts]
    assert len(ids) == len(set(ids))
    assert all(not pid.startswith("t3_") for pid in ids)


def test_selftext_is_clean(posts):
    first = posts[0]
    assert "submitted by" not in first.selftext
    assert "[link]" not in first.selftext
    assert "\n\n\n" not in first.selftext


def test_wm_prefix_parsing():
    assert parse_wm_from_title("[Hyprland] rice") == "Hyprland"
    assert parse_wm_from_title("[KDE plasma] Is this a rice..?") == "KDE plasma"
    assert parse_wm_from_title("no prefix here") is None
    assert parse_wm_from_title("[OC] neoclear") is None
    assert parse_wm_from_title("[OC] [Hyprland] rice") == "Hyprland"
    assert parse_wm_from_title("[Question] how do I rice?") is None
    assert parse_wm_from_title("[OC]") is None


def test_parse_comments_feed(posts, comments_xml):
    parsed, error = parse_comments_feed(comments_xml, posts[0])
    assert error == ""
    assert len(parsed) == 10
    assert parsed[0].author == "insanazor822"
    assert parsed[0].body.strip() == "Look great"
    assert all(c.post_id == posts[0].id for c in parsed)
    assert all(not c.id.startswith("t1_") for c in parsed)
    assert [c.ordinal for c in parsed] == list(range(len(parsed)))
    assert all("submitted by" not in c.body for c in parsed)
    # the feed's own copy of the post body must not become a comment
    assert all(posts[0].title != c.body for c in parsed)


def test_garbage_feed_raises():
    with pytest.raises(ValueError):
        parse_posts_feed("<html>not a feed</html>")


def test_window_and_url_helpers():
    assert is_supported_window("week")
    assert not is_supported_window("fortnight")
    assert (
        reddit_post_id_from_url("https://www.reddit.com/r/unixporn/comments/1abc23/title/")
        == "1abc23"
    )
    assert reddit_post_id_from_url("https://github.com/a/b") is None
