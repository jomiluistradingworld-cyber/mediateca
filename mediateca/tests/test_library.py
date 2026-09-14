"""Tests de library.py: sincronización de archivos físicos con la BD."""

from __future__ import annotations

from mediateca import db, library
from mediateca.tests.test_db import make_item


def _make_file(root, rel_path: str, content: bytes = b"hola") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_sync_imports_missing_files(tmp_path, test_config):
    root = test_config.library_path
    # Un archivo con el patrón de yt-dlp (título [id]) y su miniatura.
    _make_file(root, "youtube/Canal/Video de prueba [abc123].mp4")
    _make_file(root, "youtube/Canal/Video de prueba [abc123].jpg")
    # Un audio, suelto sin miniatura.
    _make_file(root, "youtube/Otro/Una cancion [xyz789].opus")
    # Archivos auxiliares que NO deben importarse.
    _make_file(root, "youtube/Canal/Video de prueba [abc123].part")
    _make_file(root, "youtube/Canal/Video de prueba [abc123].info.json")
    _make_file(root, "youtube/Canal/Video de prueba [abc123].jpg")

    conn = db.get_connection(test_config.db_path)
    db.init_db(conn)
    try:
        added = library.sync_library(conn, test_config)
        assert added == 2

        video = db.get_item(conn, 1)
        assert video["title"] == "Video de prueba"
        assert video["extractor"] == "youtube"
        assert video["uploader"] == "Canal"
        assert video["media_type"] == "video"
        assert video["file_path"] == "youtube/Canal/Video de prueba [abc123].mp4"
        assert video["thumbnail_path"] == "youtube/Canal/Video de prueba [abc123].jpg"
        assert video["raw_metadata"] == '{"id": "abc123"}'

        audio = db.get_item(conn, 2)
        assert audio["title"] == "Una cancion"
        assert audio["media_type"] == "audio"
        assert audio["thumbnail_path"] is None
    finally:
        conn.close()


def test_sync_is_idempotent(tmp_path, test_config):
    root = test_config.library_path
    _make_file(root, "youtube/Canal/Video [abc123].mp4")

    conn = db.get_connection(test_config.db_path)
    db.init_db(conn)
    try:
        assert library.sync_library(conn, test_config) == 1
        assert library.sync_library(conn, test_config) == 0
        assert db.count_items(conn) == 1
    finally:
        conn.close()


def test_sync_missing_library_path_returns_zero(tmp_path, test_config):
    conn = db.get_connection(test_config.db_path)
    db.init_db(conn)
    try:
        assert library.sync_library(conn, test_config) == 0
    finally:
        conn.close()


def test_insert_item_dedups_same_file_path(tmp_path):
    """Si dos descargas terminan en el mismo archivo físico (p. ej. la
    misma URL llegada por dos vías distintas), no debe crearse una fila
    duplicada: la segunda actualiza la primera y reutiliza su id."""
    conn = db.get_connection(tmp_path / "dedup.db")
    db.init_db(conn)
    try:
        one = make_item()
        two = make_item(source_url="https://itm.example.com/video1-distinto", file_path=one["file_path"])
        first = db.insert_item(conn, one)
        second = db.insert_item(conn, two)
        assert first == second
        assert db.count_items(conn) == 1
        row = db.get_item(conn, first)
        assert row["source_url"] == two["source_url"]
    finally:
        conn.close()


def test_add_from_url_skips_existing_url(tmp_path, test_config, monkeypatch):
    """Re-descargar una URL que ya está en la biblioteca no vuelve a tocar
    la red ni crea una segunda fila."""
    import mediateca.library as library_module

    calls = {"n": 0}
    original = library_module.downloader.download

    def counting(url, config, **kwargs):
        calls["n"] += 1
        return original(url, config, **kwargs)

    monkeypatch.setattr(library_module.downloader, "download", counting)

    conn = db.get_connection(test_config.db_path)
    db.init_db(conn)
    try:
        first = library.add_from_url(conn, test_config, "https://example.com/repetida")
        second = library.add_from_url(conn, test_config, "https://example.com/repetida")
        assert first == second
        assert calls["n"] == 1
        assert db.count_items(conn) == 1
    finally:
        conn.close()
