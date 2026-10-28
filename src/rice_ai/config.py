"""Runtime configuration, read from environment variables with sane defaults."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_SUBREDDIT = "unixporn"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_USER_AGENT = "rice-ai/0.1 (Linux ricing digest; +https://github.com/rice-ai)"


log = logging.getLogger(__name__)

DEFAULT_ENV_FILE = Path(".env")


def load_env(path: Path | str | None = None, *, override: bool = False) -> Path | None:
    """Load ``.env`` (or ``path``) into ``os.environ``.

    Real environment variables win by default, so ``export`` / systemd
    ``EnvironmentFile`` still override the file. Returns the file used, or
    ``None`` when it does not exist.
    """
    env_path = Path(path) if path is not None else DEFAULT_ENV_FILE
    if not env_path.is_file():
        log.debug("no env file at %s", env_path)
        return None
    load_dotenv(env_path, override=override)
    return env_path


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _env_path(name: str, default: str) -> Path:
    return Path(_env(name) or default).expanduser()


@dataclass(slots=True)
class Config:
    """Everything the pipeline needs to run; all fields overridable via env/CLI."""

    subreddit: str = DEFAULT_SUBREDDIT
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    github_token: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    db_path: Path = Path("data/rice.db")
    cache_dir: Path = Path(".cache")
    min_interval: float = 6.0  # reddit.com hosts: measured 429 threshold is low
    other_interval: float = 0.5  # images / github have no such limit
    timeout: float = 120.0
    default_limit: int = 8
    dedupe_days: int = 30
    max_comments: int = 12
    max_comment_fetches: int = 4  # comments feeds are the most rate-limited endpoint
    readme_ttl_days: int = 7

    @classmethod
    def from_env(cls) -> Config:
        cfg = cls()
        cfg.subreddit = _env("RICE_AI_SUBREDDIT") or cfg.subreddit
        cfg.model = _env("RICE_AI_MODEL") or cfg.model
        cfg.base_url = (_env("RICE_AI_BASE_URL") or cfg.base_url).rstrip("/")
        cfg.api_key = _env("DEEPSEEK_API_KEY")
        cfg.github_token = _env("GITHUB_TOKEN")
        cfg.db_path = _env_path("RICE_AI_DB", str(cfg.db_path))
        cfg.cache_dir = _env_path("RICE_AI_CACHE", str(cfg.cache_dir))
        if interval := _env("RICE_AI_MIN_INTERVAL"):
            try:
                cfg.min_interval = float(interval)
            except ValueError:
                log.warning("invalid RICE_AI_MIN_INTERVAL=%r, using %s", interval, cfg.min_interval)
        return cfg

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

