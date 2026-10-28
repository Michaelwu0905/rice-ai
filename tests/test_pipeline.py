from __future__ import annotations

import io

import pytest
from rich.console import Console

from rice_ai.llm import LlmError
from rice_ai.pipeline import DigestOptions, digest
from rice_ai.render import cards_payload
from rice_ai.store import Store


def _run(cfg, source, http, llm, **options):
    status = Console(file=io.StringIO(), width=120)
    return digest(
        cfg,
        DigestOptions(window="week", limit=3, **options),
        http=http,
        source=source,
        llm=llm,
        status=status,
    )


def test_digest_builds_cards(cfg, stub_source, stub_http, stub_llm):
    result = _run(cfg, stub_source, stub_http, stub_llm)

    assert result.fetched == 3
    assert len(result.rows) == 3
    assert result.failures == []

    store = Store(cfg.db_path)
    try:
        assert store.count_posts() == 3
        assert store.count_cards() == 3
        for row in result.rows:
            assert row.card.title_zh.startswith("中文标题")
            assert row.enrichment.palette.colors  # screenshot was analysed
        dots = [r.enrichment.dots_url for r in result.rows]
        assert any(url and "github.com" in url for url in dots)
        assert stub_source.comment_fetches >= 1
    finally:
        store.close()


def test_second_run_skips_without_llm_calls(cfg, stub_source, stub_http, stub_llm):
    first = _run(cfg, stub_source, stub_http, stub_llm)
    calls_after_first = stub_llm.call_count
    second = _run(cfg, stub_source, stub_http, stub_llm)

    assert len(first.rows) == 3
    assert second.rows == []
    assert second.skipped == 3
    assert stub_llm.call_count == calls_after_first


def test_force_bypasses_dedupe_and_cache(cfg, stub_source, stub_http, stub_llm):
    _run(cfg, stub_source, stub_http, stub_llm)
    before = stub_llm.call_count
    forced = _run(cfg, stub_source, stub_http, stub_llm, force=True)
    assert len(forced.rows) == 3
    assert stub_llm.call_count == before + 3


def test_llm_failure_falls_back_per_post(cfg, stub_source, stub_http, posts, stub_llm_cls):
    failing_id = posts[0].id
    stub_llm = stub_llm_cls(fail_for={failing_id})
    result = _run(cfg, stub_source, stub_http, stub_llm)

    by_id = {row.post.id: row for row in result.rows}
    assert by_id[failing_id].card.llm_error is True
    # enrichment still happened, so the dots link survives the LLM failure
    assert by_id[failing_id].card.dots_url == "https://github.com/Nebelung-cloud/glyph-rice"
    assert len(result.failures) == 1
    assert result.failures[0][0] == failing_id
    assert len(result.rows) == 3  # other posts still processed


def test_missing_api_key_fails_before_fetching(cfg, stub_source, stub_http, stub_llm):
    cfg.api_key = None
    before = stub_source.listing_fetches
    with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
        _run(cfg, stub_source, stub_http, stub_llm)
    assert stub_source.listing_fetches == before


def test_comment_budget_zero_still_builds_cards(cfg, stub_source, stub_http, stub_llm):
    result = _run(cfg, stub_source, stub_http, stub_llm, max_comment_fetches=0)
    assert len(result.rows) == 3
    assert stub_source.comment_fetches == 0


def test_json_payload_contract(cfg, stub_source, stub_http, stub_llm):
    result = _run(cfg, stub_source, stub_http, stub_llm)
    payload = cards_payload(result.rows)
    first = payload[0]
    for key in ("post_id", "title_zh", "summary_zh", "colors", "components", "url", "author",
                "palette", "original_title", "dots_url", "difficulty"):
        assert key in first
    assert first["url"].startswith("https://www.reddit.com/r/unixporn/comments/")
    assert isinstance(first["palette"]["colors"], list)


def test_store_roundtrip_for_show(cfg, stub_source, stub_http, stub_llm):
    result = _run(cfg, stub_source, stub_http, stub_llm)
    post_id = result.rows[0].post.id
    store = Store(cfg.db_path)
    try:
        row = store.get_card_row(post_id)
        assert row is not None
        assert row.card.title_zh == result.rows[0].card.title_zh
        assert row.enrichment.palette.hexes == result.rows[0].enrichment.palette.hexes
        assert len(store.list_cards(10)) == 3
    finally:
        store.close()


class _AuthFailLlm:
    def __init__(self):
        self.call_count = 0

    def summarize(self, post, comments, palette, readme, dots_url):
        self.call_count += 1
        raise LlmError("DeepSeek HTTP 401: Authentication Fails, invalid api key")


def test_auth_error_aborts_instead_of_writing_fallback(cfg, stub_source, stub_http):
    with pytest.raises(LlmError, match="401"):
        _run(cfg, stub_source, stub_http, _AuthFailLlm())
    store = Store(cfg.db_path)
    try:
        assert store.count_cards() == 0
    finally:
        store.close()
