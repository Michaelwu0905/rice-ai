"""Data sources. Add new implementations (e.g. Reddit OAuth) behind this protocol."""

from __future__ import annotations

from typing import Protocol

from ..models import Comment, Post

WINDOWS = ("hour", "day", "week", "month", "year", "all")


class Source(Protocol):
    name: str

    def fetch_posts(self, window: str = "week", limit: int | None = None) -> list[Post]:
        """Return the top posts for ``window``, best first."""
        ...

    def fetch_comments(self, post: Post, limit: int = 12) -> list[Comment]:
        """Return up to ``limit`` comments for ``post``."""
        ...
