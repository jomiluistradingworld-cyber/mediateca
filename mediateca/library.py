"""Orquesta config + base de datos + downloader para exponer operaciones
de alto nivel usadas tanto por la CLI como por la web."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path
from typing import Optional

from . import db, downloader
from .config import Config

# Extensiones de archivos multimedia que la biblioteca indexa. Las
# miniaturas y artefactos de descarga de yt-dlp (.part, .info.json, …) se
# ignoran: son datos auxiliares, no elementos de la biblioteca.
_MEDIA_EXTS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts", ".mka",
    ".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav", ".aac",
}
_THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav", ".aac"}
_SKIP_SUFFIXES = (".part", ".info.json", ".description", ".ytdl", ".temp")

# Los nombres de archivo que genera yt-dlp terminan en " [id]": la regex
# saca el título y el id por separado para poder indexarlos con sentido.
_ID_SUFFIX_RE = re.compile(r"^(?P<title>.+?)\s\[(?P<id>[^\[\]]+)\]$")


def open_library(config: Config) -> sqlite3.Connection:
    conn = db.get_connection(config.db_path)
    db.init_db(conn)
    return conn


def _item_from_file(root: Path, path: Path) -> dict:
    """Construye un item de la BD a partir de un archivo físico de la
    biblioteca (sin tocar la red): título, autor/carpeta, tipo, tamaño,
    miniatura hermana y fecha a partir del mtime del archivo."""
    rel = str(path.relative_to(root))
    stem = path.stem
    m = _ID_SUFFIX_RE.match(stem)
    title = m.group("title") if m else stem
    raw_id = m.group("id") if m else None

    parts = path.relative_to(root).parts
    platform = parts[0] if len(parts) > 1 else "local"
    uploader = parts[1] if len(parts) > 2 else None

    thumb_rel = None
    for ext in _THUMB_EXTS:
        cand = path.with_suffix(ext)
        if cand.exists():
            try:
                thumb_rel = str(cand.relative_to(root))
            except ValueError:
                pass
            break

    st = path.stat()
    return {
        "source_url": "",
        "extractor": platform,
        "title": title,
        "uploader": uploader,
        "upload_date": None,
        "duration": None,
        "media_type": "audio" if path.suffix.lower() in _AUDIO_EXTS else "video",
        "file_path": rel,
        "thumbnail_path": thumb_rel,
        "filesize": st.st_size,
        "ext": path.suffix.lower().lstrip(".") or None,
        "tags": [],
        "description": None,
        "added_at": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "raw_metadata": {"id": raw_id} if raw_id else {},
    }


def sync_library(conn: sqlite3.Connection, config: Config) -> int:
    """Importa a la BD los archivos multimedia que ya están en disco pero
    no están indexados todavía. No descarga nada ni borra nada: solo añade
    lo que falta. Devuelve cuántos archivos se añadieron."""
    root = config.library_path
    if not root.exists():
        return 0

    added = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(_SKIP_SUFFIXES) or path.suffix.lower() in _THUMB_EXTS:
            continue
        if path.suffix.lower() not in _MEDIA_EXTS:
            continue
        rel = str(path.relative_to(root))
        if db.find_item_by_path(conn, rel) is not None:
            continue
        db.insert_item(conn, _item_from_file(root, path))
        added += 1
    return added


def add_from_url(
    conn: sqlite3.Connection,
    config: Config,
    url: str,
    audio_only: bool = False,
    quality: Optional[str] = None,
    on_progress=None,
    cancel_event=None,
    format_id: Optional[str] = None,
    audio_format: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
) -> int:
    # Si esta URL ya se descargó, no la bajamos otra vez: devolvemos su
    # item directamente. La deduplicación por ruta de archivo dentro de
    # db.insert_item() es la red de seguridad para el caso (más raro) de
    # que la misma URL llegue escrita de otra forma (youtu.be/… vs
    # youtube.com/watch?v=…).
    existing = db.find_item_id_by_source_url(conn, url)
    if existing is not None:
        return existing

    item = downloader.download(
        url,
        config,
        audio_only=audio_only,
        quality=quality or config.default_quality,
        on_progress=on_progress,
        cancel_event=cancel_event,
        format_id=format_id,
        audio_format=audio_format,
        audio_bitrate=audio_bitrate,
    )
    return db.insert_item(conn, item)


def probe_url(config: Config, url: str) -> dict:
    """Vista previa sin descargar: metadata, formatos disponibles, o
    entradas si `url` es una lista de reproducción/canal."""
    return downloader.probe(url, config)


def list_library(
    conn: sqlite3.Connection,
    platform: Optional[str] = None,
    limit: int = 60,
    offset: int = 0,
):
    return db.list_items(conn, platform=platform, limit=limit, offset=offset)


def search_library(conn: sqlite3.Connection, query: str, limit: int = 60, offset: int = 0):
    return db.search_items(conn, query, limit=limit, offset=offset)


def get_item(conn: sqlite3.Connection, item_id: int):
    return db.get_item(conn, item_id)


def remove_item(conn: sqlite3.Connection, item_id: int, delete_file: bool = False, config: Optional[Config] = None) -> bool:
    row = db.get_item(conn, item_id)
    if row is None:
        return False
    if delete_file and config is not None:
        target = config.library_path / row["file_path"]
        if target.exists():
            target.unlink()
        if row["thumbnail_path"]:
            thumb = config.library_path / row["thumbnail_path"]
            if thumb.exists():
                thumb.unlink()
    return db.delete_item(conn, item_id)
