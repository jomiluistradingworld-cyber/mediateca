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
        opts["format"] = "bestaudio/best"
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": config.default_audio_format,
                "preferredquality": "192",
            }
        )
    else:
        opts["format"] = _quality_to_format(quality)
        opts["merge_output_format"] = "mp4"

    if config.download_thumbnails:
        opts["postprocessors"].append(
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}
        )

    return opts


def _final_filepath(info: dict) -> Optional[str]:
    downloads = info.get("requested_downloads") or []
    if downloads and downloads[0].get("filepath"):
        return downloads[0]["filepath"]
    return info.get("filepath") or info.get("_filename")


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
) -> dict[str, Any]:
    """Descarga `url` y devuelve un dict listo para db.insert_item().

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

    ydl_opts = build_ydl_opts(config, audio_only, quality, progress_hook=_hook)
    config.library_path.mkdir(parents=True, exist_ok=True)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled as e:
        raise DownloadPaused(str(e)) from e
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(str(e)) from e

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
