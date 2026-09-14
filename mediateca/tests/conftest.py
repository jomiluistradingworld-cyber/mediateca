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
        concurrent_fragments=1,
        embed_metadata=True,
        host="127.0.0.1",
        port=8420,
    )


@pytest.fixture
def app_client(monkeypatch, test_config):
    """Levanta la app FastAPI real, pero apuntando a rutas temporales
    (nunca toca ~/Mediateca ni ~/.config/mediateca de quien ejecute los
    tests)."""
    import mediateca.config as config_module
    import mediateca.web.app as app_module

    monkeypatch.setattr(app_module, "load_config", lambda: test_config)

    def _fake_ensure_dirs(cfg):
        cfg.library_path.mkdir(parents=True, exist_ok=True)
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(app_module, "ensure_dirs", _fake_ensure_dirs)

    # Config.cookies_path (y config_path) llaman a get_config_dir(), que por
    # defecto usa Path.home() de verdad. Sin este parche, un test de cookies
    # escribiría en el ~/.config/mediateca real de quien corra los tests.
    fake_config_dir = test_config.db_path.parent / "config_home"
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_module, "get_config_dir", lambda: fake_config_dir)

    app = app_module.create_app()
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client, app


@pytest.fixture(autouse=True)
def _no_real_downloads(monkeypatch):
    """Ningún test debería disparar una descarga real de red, ni siquiera
    por accidente. Esto pasó dos veces durante el desarrollo: un test que
    pega a /api/download con una URL "inofensiva" como example.com/v se ve
    inocuo en un sandbox sin salida a internet, pero en cualquier máquina
    con internet de verdad (como la que corre esto) el worker en segundo
    plano SÍ hace una petición HTTP real y tarda unos segundos en fallar,
    ensuciando la salida de pytest con un error asíncrono tardío. Con esta
    fixture autouse, ningún test necesita acordarse de mockear esto a mano.
    """
    import mediateca.downloader as downloader_module

    def fake_download(url, config, **kwargs):
        return {
            "source_url": url, "extractor": "generic", "title": "fake",
            "uploader": None, "upload_date": None, "duration": None,
            "media_type": "video", "file_path": "fake.mp4", "thumbnail_path": None,
            "filesize": None, "ext": "mp4", "tags": [], "description": None,
            "added_at": "2026-01-01T00:00:00", "raw_metadata": {},
        }

    monkeypatch.setattr(downloader_module, "download", fake_download)
