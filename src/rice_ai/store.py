"""SQLite persistence: posts, comments, enrichment, cards, dots repo cache."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .models import Comment, Palette, Post, RiceCard

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    created_utc INTEGER NOT NULL,
    permalink TEXT NOT NULL,
    selftext TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    links_json TEXT NOT NULL DEFAULT '[]',
    wm TEXT,
    window TEXT NOT NULL DEFAULT 'week',
    fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc DESC);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, ordinal);

CREATE TABLE IF NOT EXISTS enrichment (
    post_id TEXT PRIMARY KEY,
    palette_json TEXT NOT NULL DEFAULT '{}',
    dark INTEGER NOT NULL DEFAULT 1,
    dots_url TEXT,
    readme_excerpt TEXT,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    post_id TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (post_id, model)
);
CREATE INDEX IF NOT EXISTS idx_cards_created ON cards(created_at DESC);

CREATE TABLE IF NOT EXISTS dots_repos (
    url TEXT PRIMARY KEY,
    readme TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);
"""


@dataclass(slots=True)
class Enrichment:
    post_id: str
    palette: Palette
    dots_url: str | None
    readme_excerpt: str | None
    fetched_at: int


@dataclass(slots=True)
class CardRow:
    post: Post
    card: RiceCard
    enrichment: Enrichment
    created_at: int


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ posts
    def upsert_post(self, post: Post) -> bool:
        """Insert or refresh a post. Returns True when it was newly inserted."""
        exists = self.conn.execute("SELECT 1 FROM posts WHERE id = ?", (post.id,)).fetchone()
        self.conn.execute(
            """
            INSERT INTO posts (id, title, author, created_utc, permalink, selftext,
                               image_url, links_json, wm, window, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, author=excluded.author,
                created_utc=excluded.created_utc, permalink=excluded.permalink,
                selftext=excluded.selftext, image_url=excluded.image_url,
                links_json=excluded.links_json, wm=excluded.wm,
                window=excluded.window, fetched_at=excluded.fetched_at
            """,
            (
                post.id,
                post.title,
                post.author,
                post.created_utc,
                post.permalink,
                post.selftext,
                post.image_url,
                json.dumps(post.links, ensure_ascii=False),
                post.wm,
                post.window,
                int(time.time()),
            ),
        )
        self.conn.commit()
        return exists is None

    def get_post(self, post_id: str) -> Post | None:
        row = self.conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return _row_to_post(row) if row else None

    def recent_posts(self, limit: int = 20) -> list[Post]:
        rows = self.conn.execute(
            "SELECT * FROM posts ORDER BY created_utc DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_post(r) for r in rows]

    def count_posts(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0])

    # --------------------------------------------------------------- comments
    def upsert_comments(self, comments: list[Comment]) -> None:
        if not comments:
            return
        self.conn.executemany(
            """
            INSERT INTO comments (id, post_id, author, body, ordinal)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                author=excluded.author, body=excluded.body, ordinal=excluded.ordinal
            """,
            [(c.id, c.post_id, c.author, c.body, c.ordinal) for c in comments],
        )
        self.conn.commit()

    def get_comments(self, post_id: str, limit: int = 50) -> list[Comment]:
        rows = self.conn.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY ordinal LIMIT ?",
            (post_id, limit),
        ).fetchall()
        return [
            Comment(
                id=r["id"],
                post_id=r["post_id"],
                author=r["author"],
                body=r["body"],
                ordinal=r["ordinal"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------- enrichment
    def save_enrichment(
        self,
        post_id: str,
        palette: Palette,
        dots_url: str | None,
        readme_excerpt: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO enrichment (post_id, palette_json, dark, dots_url, readme_excerpt, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                palette_json=excluded.palette_json, dark=excluded.dark,
                dots_url=excluded.dots_url, readme_excerpt=excluded.readme_excerpt,
                fetched_at=excluded.fetched_at
            """,
            (
                post_id,
                json.dumps(palette.as_dict(), ensure_ascii=False),
                int(palette.dark),
                dots_url,
                readme_excerpt,
                int(time.time()),
            ),
        )
        self.conn.commit()

    def get_enrichment(self, post_id: str) -> Enrichment | None:
        row = self.conn.execute("SELECT * FROM enrichment WHERE post_id = ?", (post_id,)).fetchone()
        if not row:
            return None
        palette = Palette.from_dict(json.loads(row["palette_json"] or "{}"))
        return Enrichment(
            post_id=row["post_id"],
            palette=palette,
            dots_url=row["dots_url"],
            readme_excerpt=row["readme_excerpt"],
            fetched_at=int(row["fetched_at"]),
        )

    # ------------------------------------------------------------------ cards
    def has_recent_card(self, post_id: str, model: str, dedupe_days: int) -> bool:
        cutoff = int(time.time()) - dedupe_days * 86400
        row = self.conn.execute(
            "SELECT 1 FROM cards WHERE post_id = ? AND model = ? AND created_at >= ?",
            (post_id, model, cutoff),
        ).fetchone()
        return row is not None

    def save_card(self, card: RiceCard) -> None:
        self.conn.execute(
            """
            INSERT INTO cards (post_id, model, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(post_id, model) DO UPDATE SET
                created_at=excluded.created_at, payload_json=excluded.payload_json
            """,
            (
                card.post_id,
                card.model or "unknown",
                int(time.time()),
                json.dumps(card.as_dict(), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_card(self, post_id: str, model: str | None = None) -> RiceCard | None:
        if model:
            row = self.conn.execute(
                "SELECT * FROM cards WHERE post_id = ? AND model = ?", (post_id, model)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM cards WHERE post_id = ? ORDER BY created_at DESC", (post_id,)
            ).fetchone()
        return RiceCard.from_dict(json.loads(row["payload_json"])) if row else None

    def list_cards(self, limit: int = 20) -> list[CardRow]:
        rows = self.conn.execute(
            """
            SELECT c.payload_json, c.created_at, p.*
            FROM cards c JOIN posts p ON p.id = c.post_id
            ORDER BY c.created_at DESC, p.created_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_cardrow(row, self) for row in rows]

    def get_card_row(self, post_id: str) -> CardRow | None:
        row = self.conn.execute(
            """
            SELECT c.payload_json, c.created_at, p.*
            FROM cards c JOIN posts p ON p.id = c.post_id
            WHERE c.post_id = ?
            ORDER BY c.created_at DESC
            """,
            (post_id,),
        ).fetchone()
        return _row_to_cardrow(row, self) if row else None

    def count_cards(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])

    # ------------------------------------------------------------ dots repos
    def get_dots_repo(self, url: str, ttl_days: int) -> str | None:
        row = self.conn.execute("SELECT readme, fetched_at FROM dots_repos WHERE url = ?", (url,)).fetchone()
        if not row:
            return None
        if int(time.time()) - int(row["fetched_at"]) > ttl_days * 86400:
            return None
        return str(row["readme"])

    def save_dots_repo(self, url: str, readme: str) -> None:
        self.conn.execute(
            """
            INSERT INTO dots_repos (url, readme, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET readme=excluded.readme, fetched_at=excluded.fetched_at
            """,
            (url, readme, int(time.time())),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _row_to_post(row: sqlite3.Row) -> Post:
    return Post(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        created_utc=int(row["created_utc"]),
        permalink=row["permalink"],
        selftext=row["selftext"] or "",
        image_url=row["image_url"],
        links=json.loads(row["links_json"] or "[]"),
        wm=row["wm"],
        window=row["window"],
    )


def _row_to_cardrow(row: sqlite3.Row, store: Store) -> CardRow:
    post = _row_to_post(row)
    enrichment = store.get_enrichment(post.id) or Enrichment(
        post_id=post.id, palette=Palette(), dots_url=None, readme_excerpt=None, fetched_at=0
    )
    return CardRow(
        post=post,
        card=RiceCard.from_dict(json.loads(row["payload_json"])),
        enrichment=enrichment,
        created_at=int(row["created_at"]),
    )
