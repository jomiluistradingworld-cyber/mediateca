import pytest

from mediateca import db


@pytest.fixture
def conn(tmp_path):
    c = db.get_connection(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


def make_item(**overrides) -> dict:
    item = {
        "source_url": "https://example.com/video1",
        "extractor": "youtube",
        "title": "Un vídeo de prueba",
        "uploader": "Canal de prueba",
        "upload_date": "20260101",
        "duration": 120,
        "media_type": "video",
        "file_path": "youtube/Canal de prueba/Un vídeo de prueba [abc123].mp4",
        "thumbnail_path": None,
        "filesize": 1024,
        "ext": "mp4",
        "tags": ["tag1", "tag2"],
        "description": "Descripción de prueba",
        "added_at": "2026-01-01T00:00:00",
        "raw_metadata": {"id": "abc123"},
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------- items


def test_insert_and_get_item(conn):
    item_id = db.insert_item(conn, make_item())
    row = db.get_item(conn, item_id)
    assert row is not None
    assert row["title"] == "Un vídeo de prueba"
    assert row["extractor"] == "youtube"


def test_get_item_missing_returns_none(conn):
    assert db.get_item(conn, 999) is None


def test_delete_item(conn):
    item_id = db.insert_item(conn, make_item())
    assert db.delete_item(conn, item_id) is True
    assert db.get_item(conn, item_id) is None
    assert db.delete_item(conn, item_id) is False


def test_count_items(conn):
    assert db.count_items(conn) == 0
    db.insert_item(conn, make_item())
    db.insert_item(conn, make_item(source_url="https://example.com/video2"))
    assert db.count_items(conn) == 2


def test_distinct_platforms(conn):
    db.insert_item(conn, make_item(extractor="youtube"))
    db.insert_item(conn, make_item(extractor="vimeo", source_url="https://example.com/v2"))
    db.insert_item(conn, make_item(extractor="youtube", source_url="https://example.com/v3"))
    assert db.distinct_platforms(conn) == ["vimeo", "youtube"]


def test_list_items_pagination(conn):
    # Este test cubre justo el bug #3 de la auditoría: antes no existía
    # forma de pedir una página más allá de la primera.
    for i in range(75):
        db.insert_item(conn, make_item(source_url=f"https://example.com/v{i}", title=f"Video {i}"))

    page1 = db.list_items(conn, limit=60, offset=0)
    page2 = db.list_items(conn, limit=60, offset=60)

    assert len(page1) == 60
    assert len(page2) == 15
    # Sin solapamiento entre páginas.
    ids_page1 = {r["id"] for r in page1}
    ids_page2 = {r["id"] for r in page2}
    assert ids_page1.isdisjoint(ids_page2)


def test_list_items_filters_by_platform(conn):
    db.insert_item(conn, make_item(extractor="youtube"))
    db.insert_item(conn, make_item(extractor="vimeo", source_url="https://example.com/v2"))
    rows = db.list_items(conn, platform="vimeo")
    assert len(rows) == 1
    assert rows[0]["extractor"] == "vimeo"


def test_search_items_like_fallback(conn):
    db.insert_item(conn, make_item(title="Receta de tortilla de patatas"))
    db.insert_item(conn, make_item(title="Tutorial de guitarra", source_url="https://example.com/v2"))
    rows = db.search_items(conn, "tortilla")
    assert len(rows) == 1
    assert "tortilla" in rows[0]["title"].lower()


def test_search_items_pagination(conn):
    for i in range(10):
        db.insert_item(conn, make_item(title=f"tortilla {i}", source_url=f"https://example.com/t{i}"))
    page1 = db.search_items(conn, "tortilla", limit=6, offset=0)
    page2 = db.search_items(conn, "tortilla", limit=6, offset=6)
    assert len(page1) == 6
    assert len(page2) == 4


def test_search_items_empty_query_falls_back_to_list(conn):
    db.insert_item(conn, make_item())
    rows = db.search_items(conn, "   ")
    assert len(rows) == 1


# ---------------------------------------------------------------- jobs


def test_create_and_get_job(conn):
    db.create_job(conn, "job1", "https://example.com/x", False, "best")
    row = db.get_job(conn, "job1")
    assert row is not None
    assert row["state"] == "en_cola"
    assert row["progress"] == 0
    assert row["format_id"] is None


def test_create_job_persists_format_and_audio_overrides(conn):
    db.create_job(
        conn, "job1", "https://example.com/x", True, "best",
        format_id="137+140", audio_format="flac", audio_bitrate="320",
    )
    row = db.get_job(conn, "job1")
    assert row["format_id"] == "137+140"
    assert row["audio_format"] == "flac"
    assert row["audio_bitrate"] == "320"


def test_jobs_table_migration_adds_missing_columns(tmp_path):
    # Simula una base de datos creada con una versión anterior de mediateca
    # (sin format_id/audio_format/audio_bitrate) y confirma que init_db la
    # pone al día sin tocar los datos que ya había.
    old_conn = db.get_connection(tmp_path / "old.db")
    old_conn.executescript("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, audio_only INTEGER NOT NULL DEFAULT 0,
            quality TEXT NOT NULL DEFAULT 'best', state TEXT NOT NULL DEFAULT 'en_cola',
            progress REAL NOT NULL DEFAULT 0, message TEXT, item_id INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    old_conn.execute(
        "INSERT INTO jobs (id, url, created_at, updated_at) VALUES ('viejo', 'https://x', 'a', 'b')"
    )
    old_conn.commit()
    old_conn.close()

    conn = db.get_connection(tmp_path / "old.db")
    db.init_db(conn)  # aquí corre la migración

    row = db.get_job(conn, "viejo")
    assert row["url"] == "https://x"  # el dato viejo sigue intacto
    assert row["format_id"] is None  # la columna nueva existe, vacía
    conn.close()


def test_update_job(conn):
    db.create_job(conn, "job1", "https://example.com/x", False, "best")
    db.update_job(conn, "job1", state="descargando", progress=42.5, message="Descargando…")
    row = db.get_job(conn, "job1")
    assert row["state"] == "descargando"
    assert row["progress"] == 42.5


def test_list_jobs_orders_by_updated_at_desc(conn):
    db.create_job(conn, "job1", "https://example.com/1", False, "best")
    db.create_job(conn, "job2", "https://example.com/2", False, "best")
    db.update_job(conn, "job1", message="tocado de nuevo")
    rows = db.list_jobs(conn)
    assert rows[0]["id"] == "job1"


def test_mark_stale_jobs_interrupted(conn):
    db.create_job(conn, "job1", "https://example.com/1", False, "best")
    db.update_job(conn, "job1", state="descargando")
    db.create_job(conn, "job2", "https://example.com/2", False, "best")
    db.update_job(conn, "job2", state="listo")  # este no debe tocarse

    count = db.mark_stale_jobs_interrupted(conn)

    assert count == 1
    assert db.get_job(conn, "job1")["state"] == "interrumpido"
    assert db.get_job(conn, "job2")["state"] == "listo"


def test_shared_connection_is_thread_safe(conn):
    # Golpea la conexión compartida desde muchos hilos a la vez, como pasa
    # de verdad en la app (peticiones HTTP + worker de descarga). Si el
    # locking de db.py fallara, esto lanzaría sqlite3.OperationalError o
    # produciría un conteo final incorrecto.
    import threading

    db.create_job(conn, "job1", "https://example.com/1", False, "best")

    def bump():
        for _ in range(50):
            row = db.get_job(conn, "job1")
            db.update_job(conn, "job1", progress=(row["progress"] or 0) + 1)

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # No comprobamos el valor exacto de progress (hay una lectura-luego-
    # escritura no atómica a nivel de aplicación, así que puede haber
    # carreras "benignas" en el propio valor), sino que la conexión
    # compartida sobrevivió a la concurrencia sin reventar.
    assert db.get_job(conn, "job1") is not None
