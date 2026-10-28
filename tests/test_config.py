from __future__ import annotations

import os
from pathlib import Path

from rice_ai.config import Config, load_env


def test_load_env_reads_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\nRICE_AI_MODEL=deepseek-reasoner\n", "utf-8")

    assert load_env(env_file) == env_file
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-file"
    assert Config.from_env().model == "deepseek-reasoner"


def test_real_env_wins_over_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n", "utf-8")

    load_env(env_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-shell"


def test_override_flag_forces_file_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n", "utf-8")

    load_env(env_file, override=True)
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-file"


def test_missing_env_file_is_a_noop(tmp_path: Path):
    assert load_env(tmp_path / "nope.env") is None


def test_default_env_file_is_repo_root_dotenv(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    assert load_env() is None
    (tmp_path / ".env").write_text("RICE_AI_SUBREDDIT=rice\n", "utf-8")
    assert load_env() == Path(".env")


def test_invalid_min_interval_falls_back(monkeypatch):
    monkeypatch.setenv("RICE_AI_MIN_INTERVAL", "not-a-number")
    assert Config.from_env().min_interval == 6.0


def test_dotenv_is_gitignored():
    gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
    lines = [line.strip() for line in gitignore.read_text("utf-8").splitlines()]
    assert ".env" in lines
    assert "!.env.example" in lines
