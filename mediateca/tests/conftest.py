"""Fixtures compartidas por los tests de mediateca."""

from __future__ import annotations

import pytest

from mediateca.config import Config


@pytest.fixture
def test_config(tmp_path) -> Config:
    """Config aislada en un directorio temporal: nunca toca ~/Mediateca
    ni ~/.local/share/mediateca/ de quien ejecute los tests."""
    return Config(
        library_path=tmp_path / "library",
        db_path=tmp_path / "data" / "mediateca.db",
        default_quality="best",
        default_audio_format="mp3",
        download_thumbnails=False,
        concurrent_downloads=1,
        sleep_interval=0,
        max_sleep_interval=0,
        rate_limit_kbps=0,
        host="127.0.0.1",
        port=8420,
    )


@pytest.fixture
def app_client(monkeypatch, test_config):
    """Levanta la app FastAPI real, pero apuntando a rutas temporales
    (nunca toca ~/Mediateca ni ~/.config/mediateca de quien ejecute los
    tests)."""
    import mediateca.web.app as app_module

    monkeypatch.setattr(app_module, "load_config", lambda: test_config)

    def _fake_ensure_dirs(cfg):
        cfg.library_path.mkdir(parents=True, exist_ok=True)
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "ensure_dirs", _fake_ensure_dirs)

    app = app_module.create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client, app
