"""Envoltorio sobre yt-dlp: construye opciones, descarga y normaliza
los metadatos resultantes en un dict listo para insertar en la base
de datos.
"""

from __future__ import annotations

import datetime as dt
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from .config import Config

OUTTMPL = "%(extractor)s/%(uploader,channel,uploader_id|Desconocido)s/%(title).150B [%(id)s].%(ext)s"

# Límite de entradas que se resuelven y renderizan en la vista previa de una
# playlist. Un "radio mix" de YouTube o una lista con cientos de videos
# tardaría demasiado en resolverse y volcaría cientos de checkboxes en el
# navegador; la vista previa sirve para elegir un puñado, no para clonarlo
# entero.
MAX_PREVIEW_ENTRIES = 50

ProgressCallback = Callable[[Optional[float], str], None]


class DownloadError(RuntimeError):
    pass


class DownloadPaused(DownloadError):
    """Se lanza cuando el usuario cancela una descarga en curso a propósito
    (distinto de un fallo real: el archivo parcial se conserva y se puede
    reanudar más tarde)."""


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _quality_to_format(quality: str) -> str:
    quality = (quality or "best").lower()
    if quality in ("best", "", "auto"):
        return "bestvideo*+bestaudio/best"
    if quality.isdigit():
        h = quality
        return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
    return "bestvideo*+bestaudio/best"


def build_ydl_opts(
    config: Config,
    audio_only: bool,
    quality: str,
    progress_hook: Optional[Callable[[dict], None]] = None,
    format_id: Optional[str] = None,
    audio_format: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
) -> dict[str, Any]:
    outtmpl = str(config.library_path / OUTTMPL)

    opts: dict[str, Any] = {
        "outtmpl": outtmpl,
        "writethumbnail": bool(config.download_thumbnails),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
        "trim_file_name": 150,
        "postprocessors": [],
        # Resiliencia ante conexiones caseras/wifi inestables: yt-dlp ya
        # reintenta por defecto, pero subimos los límites y el timeout de
        # socket para que un corte breve no tumbe una descarga larga.
        "retries": 20,
        "fragment_retries": 20,
        "extractor_retries": 5,
        "socket_timeout": 30,
        "continuedl": True,
    }

    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]

    if audio_only:
        # format_id manda si el usuario eligió un formato de audio concreto
        # en la vista previa; si no, se baja el mejor audio disponible y se
        # convierte con FFmpegExtractAudio como hasta ahora.
        opts["format"] = format_id or "bestaudio/best"
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format or config.default_audio_format,
                "preferredquality": audio_bitrate or "192",
            }
        )
    else:
        opts["format"] = format_id or _quality_to_format(quality)
        opts["merge_output_format"] = "mp4"

    if config.download_thumbnails:
        opts["postprocessors"].append(
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}
        )

    if config.embed_metadata:
        # Título, autor, descripción y capítulos incrustados en el propio
        # archivo (no solo en la base de datos de mediateca).
        opts["postprocessors"].append(
            {"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True}
        )
        if config.download_thumbnails:
            # already_have_thumbnail=True: usa el archivo que ya bajamos
            # arriba (writethumbnail) y NO lo borra después de incrustarlo,
            # porque mediateca también lo necesita como miniatura de la
            # tarjeta en la biblioteca.
            opts["postprocessors"].append(
                {"key": "EmbedThumbnail", "already_have_thumbnail": True}
            )

    # Varios fragmentos del mismo video en paralelo (HLS/DASH). 1 = como
    # hasta ahora, secuencial.
    if config.concurrent_fragments > 1:
        opts["concurrent_fragment_downloads"] = config.concurrent_fragments

    # Cookies para contenido privado o restringido por edad (ver Ajustes).
    if config.cookies_path.exists():
        opts["cookiefile"] = str(config.cookies_path)

    # Mitigación de bloqueos por sitio (YouTube y otros cortan/limitan a
    # quien pide demasiado, muy seguido). Con los valores por defecto (0)
    # esto no cambia nada; se activan desde Ajustes.
    if config.sleep_interval > 0:
        opts["sleep_interval"] = config.sleep_interval
        if config.max_sleep_interval > config.sleep_interval:
            opts["max_sleep_interval"] = config.max_sleep_interval
    if config.rate_limit_kbps > 0:
        opts["ratelimit"] = config.rate_limit_kbps * 1024  # yt-dlp usa bytes/s

    return opts


def _base_probe_opts(config: Config) -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
    if config.cookies_path.exists():
        opts["cookiefile"] = str(config.cookies_path)
    return opts


def probe(url: str, config: Config) -> dict[str, Any]:
    """Consulta metadata de `url` SIN descargar nada.

    Para un video suelto: título, autor, duración, miniatura y la lista
    real de formatos disponibles (para elegir uno exacto en vez de a
    ciegas). Para una lista de reproducción o canal: cuántos videos tiene
    y el título/URL de cada uno, para poder elegir cuáles bajar.
    """
    base_opts = _base_probe_opts(config)

    try:
        # process=False: una "espiada" rápida y barata, sin resolver
        # formatos ni bajar nada, solo para saber si esto es un video
        # suelto o una lista de reproducción/canal.
        with yt_dlp.YoutubeDL(base_opts) as ydl:
            shallow = ydl.extract_info(url, download=False, process=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clarify_error(str(e))) from e

    if shallow is None:
        raise DownloadError("No se pudo obtener información de esa URL.")

    if shallow.get("_type") in ("playlist", "multi_video"):
        try:
            with yt_dlp.YoutubeDL({
                **base_opts,
                "extract_flat": "in_playlist",
                "playlistend": MAX_PREVIEW_ENTRIES,
            }) as ydl:
                full = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(_clarify_error(str(e))) from e

        entries = []
        for entry in (full or {}).get("entries") or []:
            if not entry:
                continue
            entry_url = entry.get("url") or entry.get("webpage_url")
            if not entry_url:
                continue
            entries.append(
                {
                    "url": entry_url,
                    "title": entry.get("title") or "(sin título)",
                    "duration": entry.get("duration"),
                    "thumbnail": entry.get("thumbnail"),
                }
            )
        return {
            "is_playlist": True,
            "title": (full or {}).get("title") or "Lista de reproducción",
            "uploader": (full or {}).get("uploader") or (full or {}).get("channel"),
            "duration": None,
            "thumbnail": (full or {}).get("thumbnail"),
            "entry_count": len(entries),
            "entries": entries,
            "formats": [],
        }

    # Video suelto: aquí sí hace falta la extracción completa para tener
    # la lista real de formatos (calidad, codec, si trae audio o no...).
    try:
        with yt_dlp.YoutubeDL({**base_opts, "noplaylist": True}) as ydl:
            full = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clarify_error(str(e))) from e

    if full is None:
        raise DownloadError("No se pudo obtener información de esa URL.")

    formats = []
    for f in full.get("formats") or []:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue  # entradas de solo metadata (miniaturas, storyboards…)
        height = f.get("height")
        formats.append(
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "resolution": f.get("resolution") or (f"{height}p" if height else "audio"),
                "fps": f.get("fps"),
                "vcodec": None if f.get("vcodec") == "none" else f.get("vcodec"),
                "acodec": None if f.get("acodec") == "none" else f.get("acodec"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "format_note": f.get("format_note"),
            }
        )

    return {
        "is_playlist": False,
        "title": full.get("title") or "(sin título)",
        "uploader": full.get("uploader") or full.get("channel"),
        "duration": full.get("duration"),
        "thumbnail": full.get("thumbnail"),
        "entry_count": None,
        "entries": [],
        "formats": formats,
    }


def _final_filepath(info: dict) -> Optional[str]:
    downloads = info.get("requested_downloads") or []
    if downloads and downloads[0].get("filepath"):
        return downloads[0]["filepath"]
    return info.get("filepath") or info.get("_filename")


def _clarify_error(msg: str) -> str:
    """Traduce el mensaje final de yt-dlp a algo más honesto cuando el
    motivo es un bloqueo del sitio, en vez de un simple corte de red.

    Ojo: esto solo se aplica al error FINAL, después de que yt-dlp agotó
    sus reintentos automáticos (`retries`/`fragment_retries`). Durante los
    reintentos individuales no hay forma fiable de distinguir "va a
    reintentar y funcionará" de "está bloqueado y va a seguir fallando",
    así que el aviso de progreso de cada intento sigue siendo genérico.
    """
    lowered = msg.lower()
    if "403" in msg or "forbidden" in lowered:
        return (
            "El sitio está bloqueando esta descarga (403 Forbidden). "
            "Prueba de nuevo más tarde, o baja la velocidad/paralelismo "
            "en Ajustes. Detalle original: " + msg
        )
    if "429" in msg or "too many requests" in lowered:
        return (
            "El sitio está limitando las peticiones (429 Too Many Requests). "
            "Espera un rato antes de reintentar, o baja las descargas "
            "simultáneas en Ajustes. Detalle original: " + msg
        )
    return msg


def _find_thumbnail(final_path: Path) -> Optional[Path]:
    stem_dir = final_path.parent
    stem = final_path.stem
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = stem_dir / f"{stem}.{ext}"
        if candidate.exists():
            return candidate
    return None


def download(
    url: str,
    config: Config,
    audio_only: bool = False,
    quality: str = "best",
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    format_id: Optional[str] = None,
    audio_format: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
) -> dict[str, Any]:
    """Descarga `url` y devuelve un dict listo para db.insert_item().

    `format_id` viene de la vista previa (/api/probe) cuando el usuario
    eligió un formato exacto en vez de una calidad genérica; si no se pasa,
    se usa `quality` como antes. `audio_format`/`audio_bitrate` sobrescriben
    para esta descarga puntual los valores por defecto de Ajustes.

    Si `cancel_event` se activa mientras la descarga está en curso, se
    interrumpe limpiamente (usando el mecanismo nativo de yt-dlp) dejando
    el archivo parcial en disco, y se lanza DownloadPaused en vez de
    DownloadError para que el llamador pueda distinguir "pausado a propósito"
    de "falló de verdad".
    """

    if not check_ffmpeg():
        raise DownloadError(
            "No se encontró ffmpeg en el sistema. Instálalo con "
            "'sudo apt install ffmpeg' y vuelve a intentarlo."
        )

    def _hook(d: dict) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Descarga pausada por el usuario")
        if on_progress is None:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100.0) if total else 0.0
            speed = d.get("speed")
            speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed else ""
            on_progress(min(pct, 99.0), f"Descargando {speed_str}".strip())
        elif status == "finished":
            on_progress(99.0, "Procesando (conversión/fusión con ffmpeg)…")
        elif status == "error":
            # yt-dlp ya reintenta automáticamente los cortes de red (según
            # 'retries'/'fragment_retries'); esto es un aviso de un intento
            # fallido, no necesariamente el fracaso final. No pisamos el
            # progreso ya alcanzado para no confundir con un 0% falso.
            on_progress(None, "Corte de red, reintentando automáticamente…")

    ydl_opts = build_ydl_opts(
        config, audio_only, quality, progress_hook=_hook,
        format_id=format_id, audio_format=audio_format, audio_bitrate=audio_bitrate,
    )
    config.library_path.mkdir(parents=True, exist_ok=True)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled as e:
        raise DownloadPaused(str(e)) from e
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clarify_error(str(e))) from e

    if info is None:
        raise DownloadError("yt-dlp no devolvió información del recurso.")

    final_path_str = _final_filepath(info)
    if not final_path_str:
        raise DownloadError("No se pudo determinar el archivo final descargado.")

    final_path = Path(final_path_str)
    thumb_path = _find_thumbnail(final_path)

    try:
        rel_path = final_path.relative_to(config.library_path)
    except ValueError:
        rel_path = final_path

    rel_thumb = None
    if thumb_path is not None:
        try:
            rel_thumb = thumb_path.relative_to(config.library_path)
        except ValueError:
            rel_thumb = thumb_path

    tags = info.get("tags") or info.get("categories") or []

    item = {
        "source_url": url,
        "extractor": info.get("extractor") or "desconocido",
        "title": info.get("title") or final_path.stem,
        "uploader": info.get("uploader") or info.get("channel"),
        "upload_date": info.get("upload_date"),
        "duration": int(info["duration"]) if info.get("duration") else None,
        "media_type": "audio" if audio_only else "video",
        "file_path": str(rel_path),
        "thumbnail_path": str(rel_thumb) if rel_thumb else None,
        "filesize": final_path.stat().st_size if final_path.exists() else None,
        "ext": final_path.suffix.lstrip("."),
        "tags": list(tags) if isinstance(tags, list) else [],
        "description": info.get("description"),
        "added_at": dt.datetime.now().isoformat(timespec="seconds"),
        "raw_metadata": {
            k: info.get(k)
            for k in (
                "webpage_url", "extractor_key", "id", "view_count",
                "like_count", "channel_url", "resolution", "fps",
            )
            if k in info
        },
    }

    if on_progress:
        on_progress(100.0, "Completado")

    return item
