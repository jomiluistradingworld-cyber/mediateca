"""Orquesta config + base de datos + downloader para exponer operaciones
de alto nivel usadas tanto por la CLI como por la web."""

from __future__ import annotations

import sqlite3
from typing import Optional

from . import db, downloader
from .config import Config


def open_library(config: Config) -> sqlite3.Connection:
    conn = db.get_connection(config.db_path)
    db.init_db(conn)
    return conn


def add_from_url(
    conn: sqlite3.Connection,
    config: Config,
    url: str,
    audio_only: bool = False,
    quality: Optional[str] = None,
    on_progress=None,
    cancel_event=None,
) -> int:
    item = downloader.download(
        url,
        config,
        audio_only=audio_only,
        quality=quality or config.default_quality,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    return db.insert_item(conn, item)


def list_library(conn: sqlite3.Connection, platform: Optional[str] = None, limit: int = 60):
    return db.list_items(conn, platform=platform, limit=limit)


def search_library(conn: sqlite3.Connection, query: str, limit: int = 60):
    return db.search_items(conn, query, limit=limit)


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
