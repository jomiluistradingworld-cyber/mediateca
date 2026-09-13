"""Tests para el módulo de configuración."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from mediateca.config import (
    Config,
    DEFAULTS,
    _expand,
    _toml_line,
    _write_default_config,
    ensure_dirs,
    get_config_dir,
    load_config,
    save_config,
)


# ---------------------------------------------------------------- helpers

def _make_config(**overrides) -> Config:
    kwargs = {
        "library_path": Path("/tmp/test/library"),
        "db_path": Path("/tmp/test/data/mediateca.db"),
        "default_quality": "best",
        "default_audio_format": "mp3",
        "download_thumbnails": True,
        "concurrent_downloads": 2,
        "sleep_interval": 0,
        "max_sleep_interval": 0,
        "rate_limit_kbps": 0,
        "host": "127.0.0.1",
        "port": 8420,
    }
    kwargs.update(overrides)
    return Config(**kwargs)


# ---------------------------------------------------------------- _expand


def test_expand_creates_absolute_path():
    p = _expand("~/Mediateca")
    assert p.is_absolute()
    assert str(p).startswith(str(Path.home()))


def test_expand_resolves():
    p = _expand("~/Mediateca")
    assert p == p.resolve()


# ---------------------------------------------------------------- _toml_line


def test_toml_line_bool():
    assert _toml_line("key", True) == "key = true"
    assert _toml_line("key", False) == "key = false"


def test_toml_line_int():
    assert _toml_line("key", 42) == "key = 42"


def test_toml_line_float():
    assert _toml_line("key", 3.14) == "key = 3.14"


def test_toml_line_string():
    assert _toml_line("key", "hello") == "key = \"hello\""


def test_toml_line_string_with_quotes():
    assert _toml_line("key", 'say "hi"') == "key = \"say \\\"hi\\\"\""


# ---------------------------------------------------------------- _write_default_config


def test_write_default_config_creates_file(tmp_path):
    config_path = tmp_path / "config.toml"
    _write_default_config(config_path)
    assert config_path.exists()
    content = tomllib.loads(config_path.read_text())
    assert "library_path" in content
    assert content["library_path"] == DEFAULTS["library_path"]


# ---------------------------------------------------------------- get_config_dir


def test_get_config_dir_creates_directory(tmp_path):
    with patch("mediateca.config.Path.home", return_value=tmp_path):
        d = get_config_dir()
        assert d.exists()
        assert d.name == "mediateca"


# ---------------------------------------------------------------- Config


def test_config_dataclass():
    c = _make_config()
    assert c.library_path == Path("/tmp/test/library")
    assert c.db_path == Path("/tmp/test/data/mediateca.db")
    assert c.default_quality == "best"
    assert c.download_thumbnails is True


def test_config_config_path():
    c = _make_config()
    assert c.config_path.name == "config.toml"
    assert ".config" in str(c.config_path)


# ---------------------------------------------------------------- load_config / save_config


def test_load_config_creates_default_when_missing(tmp_path):
    config_dir = tmp_path / ".config" / "mediateca"
    config_dir.mkdir(parents=True, exist_ok=True)
    with patch("mediateca.config.get_config_dir", return_value=config_dir):
        with patch("mediateca.config.Path.home", return_value=tmp_path):
            cfg = load_config()
    assert cfg.default_quality == "best"
    assert (config_dir / "config.toml").exists()


def test_load_config_merges_overrides(tmp_path):
    config_dir = tmp_path / ".config" / "mediateca"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "default_quality = \"720\"\nconcurrent_downloads = 4\n"
    )
    with patch("mediateca.config.get_config_dir", return_value=config_dir):
        with patch("mediateca.config.Path.home", return_value=tmp_path):
            cfg = load_config()
    assert cfg.default_quality == "720"
    assert cfg.concurrent_downloads == 4


def test_save_config_writes_toml(tmp_path):
    c = _make_config()
    c.library_path = tmp_path / "library"
    c.db_path = tmp_path / "data" / "db.sqlite3"
    config_dir = tmp_path / ".config" / "mediateca"
    config_dir.mkdir(parents=True, exist_ok=True)
    with patch("mediateca.config.get_config_dir", return_value=config_dir):
        save_config(c)
    content = tomllib.loads((config_dir / "config.toml").read_text())
    assert "library_path" in content
    assert "db_path" in content


def test_save_config_creates_directories(tmp_path):
    c = _make_config()
    c.library_path = tmp_path / "new" / "library"
    c.db_path = tmp_path / "new" / "data" / "db.sqlite3"
    config_dir = tmp_path / ".config" / "mediateca"
    config_dir.mkdir(parents=True, exist_ok=True)
    with patch("mediateca.config.get_config_dir", return_value=config_dir):
        save_config(c)
    assert c.library_path.exists()
    assert c.db_path.parent.exists()


# ---------------------------------------------------------------- ensure_dirs


def test_ensure_dirs_creates_both(tmp_path):
    c = _make_config()
    c.library_path = tmp_path / "lib"
    c.db_path = tmp_path / "data" / "db.sqlite3"
    ensure_dirs(c)
    assert c.library_path.exists()
    assert c.db_path.parent.exists()


def test_ensure_dirs_idempotent(tmp_path):
    c = _make_config()
    c.library_path = tmp_path / "lib"
    c.db_path = tmp_path / "data" / "db.sqlite3"
    c.library_path.mkdir(parents=True, exist_ok=True)
    c.db_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_dirs(c)
