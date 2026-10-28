from __future__ import annotations

import httpx
import pytest

from rice_ai.http import HttpClient, HttpError


def test_retries_on_429_then_succeeds(cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, content=b"ok")

    sleeps: list[float] = []
    with HttpClient(cfg, transport=httpx.MockTransport(handler), sleep=sleeps.append) as http:
        assert http.get_text("https://www.reddit.com/r/unixporn/top/.rss") == "ok"
        assert http.request_count == 2
        assert sleeps == [2.0]  # first backoff


def test_cache_prevents_second_request(cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"body")

    with HttpClient(cfg, transport=httpx.MockTransport(handler)) as http:
        assert http.get_text("https://example.com/a") == "body"
        assert http.get_text("https://example.com/a") == "body"
        assert len(calls) == 1


def test_client_error_is_not_retried(cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    with HttpClient(cfg, transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(HttpError):
            http.get_text("https://example.com/missing")
        assert len(calls) == 1


def test_stale_cache_used_when_network_keeps_failing(cfg):
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(503)
        return httpx.Response(200, content=b"cached-body")

    with HttpClient(cfg, transport=httpx.MockTransport(handler), sleep=lambda _s: None) as http:
        http.get_text("https://example.com/x", ttl=0)
        state["fail"] = True
        assert http.get_text("https://example.com/x", ttl=0) == "cached-body"


def test_retries_can_be_capped(cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429)

    with HttpClient(cfg, transport=httpx.MockTransport(handler), sleep=lambda _s: None) as http:
        with pytest.raises(HttpError, match="Reddit"):
            http.get_text("https://www.reddit.com/r/x/comments/1/.rss", ttl=None, retries=1)
        assert len(calls) == 2


def test_reddit_hosts_are_throttled_more_than_others(cfg):
    cfg.min_interval = 5.0
    cfg.other_interval = 0.0

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    sleeps: list[float] = []
    with HttpClient(cfg, transport=httpx.MockTransport(handler), sleep=sleeps.append) as http:
        http.get_text("https://external-preview.redd.it/a.png")
        assert sleeps == []
        http.get_text("https://www.reddit.com/a.rss")
        assert any(s > 4.0 for s in sleeps) is False  # first reddit hit is immediate
        http.get_text("https://www.reddit.com/b.rss")
        assert any(s > 4.0 for s in sleeps)
