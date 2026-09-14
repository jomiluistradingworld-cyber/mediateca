from mediateca import library


def test_home_page_loads(app_client):
    client, app = app_client
    res = client.get("/")
    assert res.status_code == 200


def test_download_rejects_invalid_url_scheme(app_client):
    client, app = app_client
    res = client.post("/api/download", json={"url": "javascript:alert(1)"})
    assert res.status_code == 422


def test_download_accepts_valid_url_and_creates_job(app_client):
    client, app = app_client

    # La descarga real está mockeada globalmente (ver fixture
    # _no_real_downloads en conftest.py); esto solo confirma que el job se
    # crea y queda consultable.
    res = client.post("/api/download", json={"url": "https://example.com/v"})
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    job_res = client.get(f"/api/jobs/{job_id}")
    assert job_res.status_code == 200
    assert job_res.json()["url"] == "https://example.com/v"


def test_library_pagination_via_api(app_client):
    client, app = app_client
    conn = app.state.db_conn

    for i in range(75):
        library.db.insert_item(
            conn,
            {
                "source_url": f"https://example.com/v{i}",
                "extractor": "youtube",
                "title": f"Video {i}",
                "uploader": "Canal",
                "upload_date": "20260101",
                "duration": 60,
                "media_type": "video",
                "file_path": f"youtube/Canal/Video {i} [id{i}].mp4",
                "thumbnail_path": None,
                "filesize": 100,
                "ext": "mp4",
                "tags": [],
                "description": "",
                "added_at": "2026-01-01T00:00:00",
                "raw_metadata": {},
            },
        )

    page1 = client.get("/api/library?limit=60&offset=0").json()
    page2 = client.get("/api/library?limit=60&offset=60").json()

    assert len(page1) == 60
    assert len(page2) == 15
    # Antes de la auditoría, no existía ni "offset" ni forma de ver más
    # allá de los primeros 60: esto confirma que el bug #3 quedó cerrado.
    assert {i["id"] for i in page1}.isdisjoint({i["id"] for i in page2})


def test_library_limit_is_clamped(app_client):
    client, app = app_client
    # limit=0 y limit=99999 deben rechazarse (Query(..., ge=1, le=200)),
    # no aceptarse silenciosamente.
    assert client.get("/api/library?limit=0").status_code == 422
    assert client.get("/api/library?limit=99999").status_code == 422


def test_job_not_found_returns_404(app_client):
    client, app = app_client
    assert client.get("/api/jobs/no-existe").status_code == 404


def test_job_stream_final_state_closes_immediately(app_client):
    client, app = app_client
    conn = app.state.db_conn
    library.db.create_job(conn, "jobstream1", "https://example.com/v", False, "best")
    library.db.update_job(conn, "jobstream1", state="listo", progress=100.0, message="Completado")

    with client.stream("GET", "/api/jobs/jobstream1/stream") as response:
        assert response.status_code == 200
        content = "".join(response.iter_text())

    assert '"state":"listo"' in content.replace(" ", "")
    assert '"job_id":"jobstream1"' in content.replace(" ", "")


def test_job_stream_unknown_job_sends_not_found(app_client):
    client, app = app_client
    with client.stream("GET", "/api/jobs/no-existe/stream") as response:
        content = "".join(response.iter_text())
    assert "event: not_found" in content


def test_probe_endpoint_returns_video_info(app_client, monkeypatch):
    client, app = app_client
    import mediateca.web.app as app_module

    fake_result = {
        "is_playlist": False, "title": "Video de prueba", "uploader": "Canal",
        "duration": 120, "thumbnail": None, "entry_count": None, "entries": [],
        "formats": [{"format_id": "137", "ext": "mp4", "resolution": "1080p",
                      "fps": 30, "vcodec": "avc1", "acodec": None,
                      "filesize": 100, "format_note": None}],
    }
    monkeypatch.setattr(app_module.library, "probe_url", lambda cfg, url: fake_result)

    res = client.post("/api/probe", json={"url": "https://example.com/v"})
    assert res.status_code == 200
    assert res.json()["title"] == "Video de prueba"


def test_probe_endpoint_rejects_bad_url(app_client):
    client, app = app_client
    res = client.post("/api/probe", json={"url": "javascript:alert(1)"})
    assert res.status_code == 422


def test_probe_endpoint_surfaces_download_error(app_client, monkeypatch):
    client, app = app_client
    import mediateca.web.app as app_module
    from mediateca.downloader import DownloadError

    def boom(cfg, url):
        raise DownloadError("ese video no existe")

    monkeypatch.setattr(app_module.library, "probe_url", boom)
    res = client.post("/api/probe", json={"url": "https://example.com/v"})
    assert res.status_code == 422
    assert "no existe" in res.json()["detail"]


def test_download_persists_format_overrides_in_job(app_client):
    client, app = app_client
    res = client.post(
        "/api/download",
        json={"url": "https://example.com/v", "audio_only": True,
              "format_id": "137+140", "audio_format": "flac", "audio_bitrate": "320"},
    )
    job_id = res.json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["format_id"] == "137+140"
    assert job["audio_format"] == "flac"
    assert job["audio_bitrate"] == "320"


def test_settings_save_persists_new_fields(app_client, monkeypatch):
    client, app = app_client
    import mediateca.web.app as app_module

    monkeypatch.setattr(app_module, "save_config", lambda cfg: None)
    res = client.post(
        "/settings",
        data={
            "library_path": str(app.state.config.library_path),
            "default_quality": "best",
            "default_audio_format": "mp3",
            "concurrent_downloads": "1",
            "concurrent_fragments": "5",
            "embed_metadata": "on",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert app.state.config.concurrent_fragments == 5
    assert app.state.config.embed_metadata is True


def test_settings_save_uploads_and_removes_cookies(app_client, monkeypatch):
    client, app = app_client
    import mediateca.web.app as app_module

    monkeypatch.setattr(app_module, "save_config", lambda cfg: None)

    res = client.post(
        "/settings",
        data={
            "library_path": str(app.state.config.library_path),
            "default_quality": "best",
            "default_audio_format": "mp3",
            "concurrent_downloads": "1",
        },
        files={"cookies_file": ("cookies.txt", b"# Netscape HTTP Cookie File\n", "text/plain")},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert app.state.config.cookies_path.exists()

    res2 = client.post(
        "/settings",
        data={
            "library_path": str(app.state.config.library_path),
            "default_quality": "best",
            "default_audio_format": "mp3",
            "concurrent_downloads": "1",
            "remove_cookies": "on",
        },
        follow_redirects=False,
    )
    assert res2.status_code == 303
    assert not app.state.config.cookies_path.exists()
    client, app = app_client
    import mediateca.web.app as app_module

    # No queremos escribir de verdad en ~/.config/mediateca durante el test.
    monkeypatch.setattr(app_module, "save_config", lambda cfg: None)

    old_executor = app.state.executor
    res = client.post(
        "/settings",
        data={
            "library_path": str(app.state.config.library_path),
            "default_quality": "best",
            "default_audio_format": "mp3",
            "concurrent_downloads": "3",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert app.state.config.concurrent_downloads == 3
    # Este es justo el bug #1 de la auditoría: antes el executor viejo (con
    # el tamaño anterior) seguía siendo el mismo objeto para siempre.
    assert app.state.executor is not old_executor


def test_rescan_imports_disk_files_via_api(app_client, tmp_path):
    client, app = app_client

    # Un archivo físico en la carpeta configurada de la biblioteca.
    video = app.state.config.library_path / "youtube" / "Canal" / "Prueba [abc123].mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"prueba")

    res = client.post("/api/rescan")
    assert res.status_code == 200
    assert res.json()["added"] == 1

    conn = app.state.db_conn
    row = conn.execute("SELECT * FROM items WHERE file_path = ?", (
        "youtube/Canal/Prueba [abc123].mp4",
    )).fetchone()
    assert row is not None
    assert row["title"] == "Prueba"

    # La segunda vez no añade nada (idempotente).
    assert client.post("/api/rescan").json()["added"] == 0
