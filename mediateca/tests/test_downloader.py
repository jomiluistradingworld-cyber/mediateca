"""Tests de downloader.py.

No podemos golpear sitios reales de video desde este entorno de pruebas
(sin acceso de red saliente a ellos), así que probe() se prueba
simulando yt_dlp.YoutubeDL con datos enlatados. build_ydl_opts() no
necesita red: solo arma un diccionario de opciones.
"""

from __future__ import annotations

from mediateca import downloader


def make_config(tmp_path, **overrides):
    from mediateca.config import Config

    base = dict(
        library_path=tmp_path / "library",
        db_path=tmp_path / "data" / "mediateca.db",
        default_quality="best",
        default_audio_format="mp3",
        download_thumbnails=True,
        concurrent_downloads=1,
        sleep_interval=0,
        max_sleep_interval=0,
        rate_limit_kbps=0,
        concurrent_fragments=1,
        embed_metadata=True,
        host="127.0.0.1",
        port=8420,
    )
    base.update(overrides)
    return Config(**base)


# ---------------------------------------------------------------- build_ydl_opts


def test_build_opts_video_uses_quality_by_default(tmp_path):
    cfg = make_config(tmp_path)
    opts = downloader.build_ydl_opts(cfg, audio_only=False, quality="720")
    assert "720" in opts["format"]


def test_build_opts_format_id_overrides_quality(tmp_path):
    cfg = make_config(tmp_path)
    opts = downloader.build_ydl_opts(cfg, audio_only=False, quality="720", format_id="137+140")
    assert opts["format"] == "137+140"


def test_build_opts_audio_only_uses_config_defaults(tmp_path):
    cfg = make_config(tmp_path, default_audio_format="opus")
    opts = downloader.build_ydl_opts(cfg, audio_only=True, quality="best")
    extract_pp = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract_pp["preferredcodec"] == "opus"
    assert extract_pp["preferredquality"] == "192"


def test_build_opts_audio_override_beats_config_default(tmp_path):
    cfg = make_config(tmp_path, default_audio_format="mp3")
    opts = downloader.build_ydl_opts(
        cfg, audio_only=True, quality="best", audio_format="flac", audio_bitrate="320"
    )
    extract_pp = next(p for p in opts["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract_pp["preferredcodec"] == "flac"
    assert extract_pp["preferredquality"] == "320"


def test_build_opts_embed_metadata_adds_postprocessors(tmp_path):
    cfg = make_config(tmp_path, embed_metadata=True, download_thumbnails=True)
    opts = downloader.build_ydl_opts(cfg, audio_only=False, quality="best")
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegMetadata" in keys
    assert "EmbedThumbnail" in keys
    # already_have_thumbnail=True es lo que evita que se borre la miniatura
    # suelta que mediateca necesita para la tarjeta de la biblioteca.
    embed_pp = next(p for p in opts["postprocessors"] if p["key"] == "EmbedThumbnail")
    assert embed_pp["already_have_thumbnail"] is True


def test_build_opts_no_embed_thumbnail_without_thumbnails(tmp_path):
    # Sin miniatura descargada no hay nada que incrustar como carátula.
    cfg = make_config(tmp_path, embed_metadata=True, download_thumbnails=False)
    opts = downloader.build_ydl_opts(cfg, audio_only=False, quality="best")
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "EmbedThumbnail" not in keys
    assert "FFmpegMetadata" in keys


def test_build_opts_embed_metadata_off(tmp_path):
    cfg = make_config(tmp_path, embed_metadata=False)
    opts = downloader.build_ydl_opts(cfg, audio_only=False, quality="best")
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegMetadata" not in keys
    assert "EmbedThumbnail" not in keys


def test_build_opts_concurrent_fragments(tmp_path):
    cfg = make_config(tmp_path, concurrent_fragments=1)
    assert "concurrent_fragment_downloads" not in downloader.build_ydl_opts(cfg, False, "best")

    cfg2 = make_config(tmp_path, concurrent_fragments=4)
    opts = downloader.build_ydl_opts(cfg2, False, "best")
    assert opts["concurrent_fragment_downloads"] == 4


def test_build_opts_cookies_only_when_file_exists(tmp_path, monkeypatch):
    import mediateca.config as config_module

    monkeypatch.setattr(config_module, "get_config_dir", lambda: tmp_path / "config_home")

    cfg = make_config(tmp_path)
    opts = downloader.build_ydl_opts(cfg, False, "best")
    assert "cookiefile" not in opts

    cfg.cookies_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.cookies_path.write_text("# Netscape HTTP Cookie File\n")
    opts2 = downloader.build_ydl_opts(cfg, False, "best")
    assert opts2["cookiefile"] == str(cfg.cookies_path)


# ---------------------------------------------------------------- probe()


class _FakeYDL:
    """Sustituye a yt_dlp.YoutubeDL en los tests: no toca la red, devuelve
    lo que le digamos según la URL, igual que haría el sitio real."""

    _RESPONSES: dict = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False, process=True):
        response = self._RESPONSES[url]
        # La llamada "superficial" (process=False) solo necesita el _type;
        # la llamada completa (o con extract_flat) necesita el resto.
        if process is False:
            return {"_type": response.get("_type", "video")}
        return response


def test_probe_single_video(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    url = "https://example.com/watch?v=abc123"
    _FakeYDL._RESPONSES = {
        url: {
            "_type": "video",
            "title": "Mi video de prueba",
            "uploader": "Canal de prueba",
            "duration": 245,
            "thumbnail": "https://example.com/thumb.jpg",
            "formats": [
                {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 123456},
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 5000},
                {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},  # storyboard: se descarta
            ],
        }
    }
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)

    result = downloader.probe(url, cfg)

    assert result["is_playlist"] is False
    assert result["title"] == "Mi video de prueba"
    assert result["duration"] == 245
    # El formato "solo metadata" (sin video ni audio) se descarta.
    assert len(result["formats"]) == 2
    assert {f["format_id"] for f in result["formats"]} == {"137", "140"}


def test_probe_playlist(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    url = "https://example.com/playlist?list=xyz"
    _FakeYDL._RESPONSES = {
        url: {
            "_type": "playlist",
            "title": "Mi lista de reproducción",
            "uploader": "Canal de prueba",
            "entries": [
                {"url": "https://example.com/watch?v=1", "title": "Video 1", "duration": 100},
                {"url": "https://example.com/watch?v=2", "title": "Video 2", "duration": 200},
                None,  # entradas privadas/borradas: yt-dlp a veces devuelve None
            ],
        }
    }
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)

    result = downloader.probe(url, cfg)

    assert result["is_playlist"] is True
    assert result["entry_count"] == 2
    assert [e["title"] for e in result["entries"]] == ["Video 1", "Video 2"]
    assert result["formats"] == []
