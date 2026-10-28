"""Shared HTTP client: polite rate limiting, retries with backoff, disk cache.

Reddit blocks fast anonymous clients (429), so every request goes through one
place that enforces a minimum interval, retries transient failures, and caches
responses on disk. The cache doubles as an offline/debugging tool.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

import httpx

from .config import Config

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFFS = (2.0, 6.0, 18.0)
MAX_RETRY_AFTER = 60.0


class HttpError(RuntimeError):
    """Raised when a request cannot be satisfied even after retries."""


def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class HttpClient:
    def __init__(
        self,
        cfg: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.sleep = sleep
        self.request_count = 0
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(cfg.timeout, connect=20.0),
            transport=transport,
            headers={
                "User-Agent": cfg.user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    # ------------------------------------------------------------------ utils
    def _interval_for(self, url: str) -> float:
        host = (urlparse(url).hostname or "").lower()
        if host == "reddit.com" or host.endswith(".reddit.com"):
            return self.cfg.min_interval
        return self.cfg.other_interval

    def _throttle(self, url: str) -> None:
        interval = self._interval_for(url)
        if interval <= 0:
            return
        host = urlparse(url).hostname or ""
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < interval:
            self.sleep(interval - elapsed)

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = cache_key(url)
        folder = self.cfg.cache_dir / "http" / key[:2]
        return folder / f"{key}.bin", folder / f"{key}.json"

    def _read_cache(self, url: str, ttl: float | None) -> bytes | None:
        body_path, meta_path = self._cache_paths(url)
        if not (body_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if ttl is not None and time.time() - float(meta.get("fetched_at", 0)) > ttl:
            return None
        return body_path.read_bytes()

    def read_stale_cache(self, url: str) -> bytes | None:
        return self._read_cache(url, None)

    def _write_cache(self, url: str, body: bytes, content_type: str) -> None:
        body_path, meta_path = self._cache_paths(url)
        try:
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(body)
            meta_path.write_text(
                json.dumps(
                    {"url": url, "fetched_at": time.time(), "content_type": content_type},
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        except OSError as exc:  # cache is best-effort, never fatal
            log.debug("cache write failed for %s: %s", url, exc)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return min(float(raw), MAX_RETRY_AFTER)
        except ValueError:
            return None

    # ------------------------------------------------------------------- main
    def get(
        self,
        url: str,
        *,
        ttl: float | None = 900.0,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
    ) -> bytes:
        """Fetch ``url`` as bytes, using a fresh cache entry when available.

        ``ttl=None`` disables the cache read (still writes). On exhausted
        retries, a stale cache entry is used if one exists. ``retries`` caps
        the retry attempts (default: retry every entry in ``BACKOFFS``).
        """
        cached = self._read_cache(url, ttl) if ttl is not None else None
        if cached is not None:
            log.debug("cache hit %s", url)
            return cached

        last_error: str | None = None
        last_status: int | None = None
        backoffs = BACKOFFS if retries is None else BACKOFFS[: max(0, retries)]
        for attempt, backoff in enumerate((0.0, *backoffs)):
            if backoff:
                self.sleep(backoff)
            self._throttle(url)
            try:
                self.request_count += 1
                self._last_request[urlparse(url).hostname or ""] = time.monotonic()
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("request failed (%s/%d) %s: %s", attempt + 1, len(backoffs) + 1, url, last_error)
                continue

            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                last_status = response.status_code
                wait = self._retry_after(response)
                log.warning(
                    "retryable status %s (%d/%d) %s%s",
                    response.status_code,
                    attempt + 1,
                    len(backoffs) + 1,
                    url,
                    f" retry-after={wait}s" if wait else "",
                )
                if wait:
                    self.sleep(wait)
                continue

            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
                raise HttpError(f"{url} -> {last_error}")

            body = response.content
            self._write_cache(url, body, response.headers.get("content-type", ""))
            return body

        stale = self.read_stale_cache(url)
        if stale is not None:
            log.warning("using stale cache for %s after failures (%s)", url, last_error)
            return stale
        hint = "（Reddit 限流，等 1 分钟再试）" if last_status == 429 else ""
        raise HttpError(f"{url} failed after retries: {last_error}{hint}")

    def get_text(
        self,
        url: str,
        *,
        ttl: float | None = 900.0,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
    ) -> str:
        return self.get(url, ttl=ttl, headers=headers, retries=retries).decode("utf-8", errors="replace")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
