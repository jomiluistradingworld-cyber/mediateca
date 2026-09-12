from mediateca import library


def test_home_page_loads(app_client):
    client, app = app_client
    res = client.get("/")
    assert res.status_code == 200


def test_download_rejects_invalid_url_scheme(app_client):
    client, app = app_client
    res = client.post("/api/download", json={"url": "javascript:alert(1)"})
    assert res.status_code == 422


def test_download_accepts_valid_url_and_creates_job(app_client, monkeypatch):
    client, app = app_client

    # No queremos que el test dispare una descarga real de yt-dlp: el
    # worker corre en un hilo del executor, así que basta con dejar que el
    # job se cree; no hace falta esperar a que "termine" para este test.
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


def test_settings_save_rebuilds_executor_on_concurrency_change(app_client, monkeypatch):
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
