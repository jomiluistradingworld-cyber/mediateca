"""Servidor web local de mediateca. Se levanta con `mediateca serve`."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile as StarletteUploadFile

from .. import library
from ..config import Config, ensure_dirs, load_config, save_config
from ..downloader import DownloadError, DownloadPaused
from ..formatting import fmt_duration, fmt_size
from ..logging_setup import configure_logging
from ..models import DownloadRequest, JobStatus, ProbeRequest, ProbeResult

BASE_DIR = Path(__file__).parent

# Los trabajos de descarga viven en la tabla `jobs` de la base de datos (ver
# db.py), no en memoria: así el progreso sobrevive a recargas de página y a
# reinicios del servidor. Lo único que sí es puramente de este proceso son
# los "cancel_event" de las descargas activas en este instante, porque solo
# tienen sentido mientras el hilo que las ejecuta sigue vivo.
CANCEL_EVENTS: dict[str, threading.Event] = {}
CANCEL_EVENTS_LOCK = threading.Lock()

RESUMABLE_STATES = ("pausado", "error", "interrumpido")
ACTIVE_STATES = ("en_cola", "descargando", "procesando")


def _row_to_dict(row) -> dict:
    d = {k: row[k] for k in row.keys()}
    for json_field in ("tags", "raw_metadata"):
        raw = d.get(json_field)
        if isinstance(raw, str):
            try:
                d[json_field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[json_field] = [] if json_field == "tags" else {}
        elif raw is None:
            d[json_field] = [] if json_field == "tags" else {}
    return d


def _job_to_dict(row) -> dict:
    return {
        "job_id": row["id"],
        "url": row["url"],
        "audio_only": bool(row["audio_only"]),
        "quality": row["quality"],
        "format_id": row["format_id"],
        "audio_format": row["audio_format"],
        "audio_bitrate": row["audio_bitrate"],
        "state": row["state"],
        "progress": row["progress"],
        "message": row["message"],
        "item_id": row["item_id"],
    }


def create_app() -> FastAPI:
    config = load_config()
    ensure_dirs(config)

    logger = configure_logging(config.db_path.parent)
    logger.info("Arrancando mediateca (host=%s puerto=%s)", config.host, config.port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.db_conn.close()

    app = FastAPI(title="mediateca", lifespan=lifespan)
    app.state.config = config
    app.state.logger = logger
    app.state.executor = ThreadPoolExecutor(max_workers=max(1, config.concurrent_downloads))

    # Una única conexión SQLite para todo el proceso, compartida entre las
    # peticiones HTTP y los hilos worker de descarga (ver get_conn() más
    # abajo). Antes se abría una conexión nueva en cada llamada a get_conn()
    # y nunca se cerraba: con el polling de /api/jobs/<id> eso agotaba los
    # descriptores de archivo disponibles y terminaba en
    # "sqlite3.OperationalError: unable to open database file". db.py
    # serializa el acceso concurrente a esta conexión con su propio lock.
    app.state.db_conn = library.open_library(config)

    # Cualquier job que haya quedado "en curso" es de un proceso anterior que
    # murió (este proceso recién arranca): lo marcamos como interrumpido en
    # vez de dejarlo mintiendo para siempre sobre su propio progreso.
    library.db.mark_stale_jobs_interrupted(app.state.db_conn)

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["duration"] = fmt_duration
    templates.env.filters["filesize"] = fmt_size
    templates.env.filters["urlpath"] = lambda p: quote(str(p), safe="/") if p else ""
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    async def _serve_media(scope, receive, send):
        # Antes este mount se creaba UNA vez con app.mount(..., StaticFiles(directory=...)),
        # fijando para siempre la carpeta que hubiera en config.library_path al arrancar.
        # Si luego cambiabas la carpeta de biblioteca desde Ajustes, /media seguía
        # sirviendo desde la ruta vieja hasta reiniciar el proceso a mano. Al construir
        # el handler de nuevo en cada petición, siempre refleja app.state.config actual.
        handler = StaticFiles(directory=str(app.state.config.library_path), check_dir=False)
        await handler(scope, receive, send)

    app.mount("/media", _serve_media, name="media")

    def get_conn():
        # Ya no abre una conexión nueva: devuelve la única conexión del
        # proceso, creada arriba al levantar la app.
        return app.state.db_conn

    def _worker(
        job_id: str,
        url: str,
        audio_only: bool,
        quality: str,
        format_id: Optional[str] = None,
        audio_format: Optional[str] = None,
        audio_bitrate: Optional[str] = None,
    ) -> None:
        conn = get_conn()
        cancel_event = threading.Event()
        with CANCEL_EVENTS_LOCK:
            CANCEL_EVENTS[job_id] = cancel_event

        library.db.update_job(conn, job_id, state="descargando", message="Descargando…")

        # Throttle: escribir en la base de datos en cada tick de progreso de
        # yt-dlp sería demasiado; solo persistimos si cambió bastante el
        # porcentaje o pasó al menos un segundo (los mensajes de reintento,
        # al ser poco frecuentes, siempre se escriben).
        last = {"pct": -1.0, "ts": 0.0}

        def on_progress(pct: Optional[float], message: str) -> None:
            now = time.monotonic()
            if pct is not None:
                if pct - last["pct"] >= 1.0 or now - last["ts"] >= 1.0 or pct >= 99:
                    state = "procesando" if pct >= 99 else "descargando"
                    library.db.update_job(conn, job_id, progress=pct, message=message, state=state)
                    last["pct"] = pct
                    last["ts"] = now
            else:
                library.db.update_job(conn, job_id, message=message)
                last["ts"] = now

        try:
            item_id = library.add_from_url(
                conn,
                app.state.config,
                url,
                audio_only=audio_only,
                quality=quality,
                on_progress=on_progress,
                cancel_event=cancel_event,
                format_id=format_id,
                audio_format=audio_format,
                audio_bitrate=audio_bitrate,
            )
            library.db.update_job(
                conn, job_id, state="listo", progress=100.0, message="Completado", item_id=item_id
            )
        except DownloadPaused:
            library.db.update_job(
                conn, job_id, state="pausado",
                message="Pausada. Al reanudar continúa desde donde se quedó.",
            )
        except DownloadError as e:
            logger.warning("Job %s terminó en error: %s", job_id, e)
            library.db.update_job(conn, job_id, state="error", message=str(e))
        except Exception as e:  # salvaguarda: nunca dejar el job colgado
            logger.exception("Job %s: error inesperado", job_id)
            library.db.update_job(conn, job_id, state="error", message=f"Error inesperado: {e}")
        finally:
            with CANCEL_EVENTS_LOCK:
                CANCEL_EVENTS.pop(job_id, None)

    # ---------------------------------------------------------------- páginas

    @app.get("/")
    def home(request: Request):
        conn = get_conn()
        rows = [_row_to_dict(r) for r in library.list_library(conn, limit=60)]
        platforms = library.db.distinct_platforms(conn)
        total = library.db.count_items(conn)
        return templates.TemplateResponse(
            request,
            "library.html",
            {"items": rows, "platforms": platforms, "total": total},
        )

    @app.get("/download")
    def download_page(request: Request):
        return templates.TemplateResponse(
            request, "download.html", {"config": app.state.config}
        )

    @app.get("/item/{item_id}")
    def item_page(request: Request, item_id: int):
        conn = get_conn()
        row = library.get_item(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Elemento no encontrado")
        return templates.TemplateResponse(
            request, "item.html", {"item": _row_to_dict(row)}
        )

    @app.get("/settings")
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html",
            {"config": app.state.config, "cookies_configured": app.state.config.cookies_path.exists()},
        )

    @app.post("/settings")
    async def settings_save(request: Request):
        form = await request.form()
        cfg = app.state.config
        new_cfg = Config(
            library_path=Path(str(form.get("library_path", cfg.library_path))).expanduser(),
            db_path=cfg.db_path,
            default_quality=str(form.get("default_quality", cfg.default_quality)),
            default_audio_format=str(form.get("default_audio_format", cfg.default_audio_format)),
            download_thumbnails="download_thumbnails" in form,
            concurrent_downloads=max(1, int(form.get("concurrent_downloads", cfg.concurrent_downloads))),
            sleep_interval=max(0.0, float(form.get("sleep_interval", cfg.sleep_interval) or 0)),
            max_sleep_interval=max(0.0, float(form.get("max_sleep_interval", cfg.max_sleep_interval) or 0)),
            rate_limit_kbps=max(0, int(form.get("rate_limit_kbps", cfg.rate_limit_kbps) or 0)),
            concurrent_fragments=max(1, int(form.get("concurrent_fragments", cfg.concurrent_fragments) or 1)),
            embed_metadata="embed_metadata" in form,
            host=cfg.host,
            port=cfg.port,
        )
        save_config(new_cfg)
        app.state.config = new_cfg

        # Cookies (contenido privado/restringido por edad): archivo aparte,
        # no un valor de config.toml (ver Config.cookies_path). Si llegó un
        # archivo nuevo lo guardamos con permisos restrictivos (son datos
        # sensibles, equivalentes a estar logueado); si pidieron quitarlas,
        # se borra el archivo.
        cookies_upload = form.get("cookies_file")
        # form.get() devuelve UploadFile | str | None. Ojo: es
        # starlette.datastructures.UploadFile (lo que Request.form()
        # realmente produce), NO fastapi.UploadFile — son clases distintas
        # sin relación de herencia en esta versión, así que comparar contra
        # la de fastapi nunca daría True aquí.
        if isinstance(cookies_upload, StarletteUploadFile) and cookies_upload.filename:
            content = await cookies_upload.read()
            if content.strip():
                new_cfg.cookies_path.parent.mkdir(parents=True, exist_ok=True)
                new_cfg.cookies_path.write_bytes(content)
                try:
                    new_cfg.cookies_path.chmod(0o600)
                except OSError:
                    pass
                logger.info("Cookies actualizadas (%d bytes)", len(content))
        if form.get("remove_cookies"):
            new_cfg.cookies_path.unlink(missing_ok=True)
            logger.info("Cookies eliminadas")

        # El ThreadPoolExecutor no se puede redimensionar en caliente: si
        # cambió el número de descargas simultáneas, hay que crear uno nuevo.
        # shutdown(wait=False) no cancela los jobs que ya estén corriendo en
        # el executor viejo, solo deja de aceptar trabajos nuevos en él.
        if new_cfg.concurrent_downloads != cfg.concurrent_downloads:
            old_executor = app.state.executor
            app.state.executor = ThreadPoolExecutor(max_workers=new_cfg.concurrent_downloads)
            old_executor.shutdown(wait=False)
            logger.info(
                "concurrent_downloads cambiado de %s a %s: executor reconstruido",
                cfg.concurrent_downloads, new_cfg.concurrent_downloads,
            )

        return RedirectResponse(url="/settings?saved=1", status_code=303)

    # ---------------------------------------------------------------- API

    @app.get("/api/library")
    def api_library(
        q: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = Query(60, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        conn = get_conn()
        if q:
            rows = library.search_library(conn, q, limit=limit, offset=offset)
        else:
            rows = library.list_library(conn, platform=platform, limit=limit, offset=offset)
        return [_row_to_dict(r) for r in rows]

    @app.delete("/api/items/{item_id}")
    def api_delete_item(item_id: int, delete_file: bool = False):
        conn = get_conn()
        ok = library.remove_item(conn, item_id, delete_file=delete_file, config=app.state.config)
        if not ok:
            raise HTTPException(status_code=404, detail="Elemento no encontrado")
        return {"ok": True}

    @app.post("/api/probe", response_model=ProbeResult)
    def api_probe(req: ProbeRequest):
        """Vista previa sin descargar: metadata + formatos reales de un
        video suelto, o la lista de entradas si la URL es una lista de
        reproducción/canal."""
        try:
            return library.probe_url(app.state.config, req.url)
        except DownloadError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    @app.post("/api/download")
    def api_download(req: DownloadRequest):
        job_id = uuid.uuid4().hex[:12]
        quality = req.quality or app.state.config.default_quality
        conn = get_conn()
        library.db.create_job(
            conn, job_id, req.url, req.audio_only, quality,
            format_id=req.format_id, audio_format=req.audio_format, audio_bitrate=req.audio_bitrate,
        )
        app.state.executor.submit(
            _worker, job_id, req.url, req.audio_only, quality,
            req.format_id, req.audio_format, req.audio_bitrate,
        )
        return {"job_id": job_id}

    @app.get("/api/jobs", response_model=list[JobStatus])
    def api_jobs_list(limit: int = Query(20, ge=1, le=200)):
        conn = get_conn()
        rows = library.db.list_jobs(conn, limit=limit)
        return [_job_to_dict(r) for r in rows]

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    def api_job(job_id: str):
        conn = get_conn()
        row = library.db.get_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        return _job_to_dict(row)

    @app.get("/api/jobs/{job_id}/stream")
    async def api_job_stream(job_id: str, request: Request):
        """Server-Sent Events con el progreso de un job.

        Sustituye al polling por setInterval del frontend (1 petición HTTP
        por segundo, por cada job activo, por cada pestaña abierta) por una
        única conexión persistente. El servidor solo manda un evento nuevo
        cuando algo cambió, y cierra solo cuando el job llega a un estado
        final o el cliente se desconecta.
        """

        async def event_generator():
            last_payload = None
            while True:
                if await request.is_disconnected():
                    return
                conn = get_conn()
                row = library.db.get_job(conn, job_id)
                if row is None:
                    yield "event: not_found\ndata: {}\n\n"
                    return
                job = _job_to_dict(row)
                payload = json.dumps(job)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                if job["state"] not in ACTIVE_STATES:
                    return
                await asyncio.sleep(0.7)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/pause")
    def api_pause_job(job_id: str):
        with CANCEL_EVENTS_LOCK:
            event = CANCEL_EVENTS.get(job_id)
        if event is None:
            raise HTTPException(
                status_code=404, detail="No hay una descarga activa con ese id ahora mismo"
            )
        event.set()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/resume")
    def api_resume_job(job_id: str):
        conn = get_conn()
        row = library.db.get_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        if row["state"] not in RESUMABLE_STATES:
            raise HTTPException(
                status_code=400, detail="Este trabajo no está pausado, en error, ni interrumpido"
            )
        library.db.update_job(conn, job_id, state="en_cola", message="Reanudando…")
        app.state.executor.submit(
            _worker, job_id, row["url"], bool(row["audio_only"]), row["quality"],
            row["format_id"], row["audio_format"], row["audio_bitrate"],
        )
        return {"job_id": job_id}

    return app
