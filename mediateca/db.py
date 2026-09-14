"""Capa de acceso a datos. SQLite puro, sin ORM, para mantenerlo simple.

Incluye búsqueda de texto completo (FTS5) cuando el SQLite del sistema
la soporta, con un fallback automático a búsqueda con LIKE si no.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

# La app web comparte UNA sola conexión entre el hilo de FastAPI y los hilos
# worker de descarga (ver web/app.py). sqlite3.Connection no está pensada
# para que varios hilos la usen a la vez sin coordinarse, así que serializamos
# aquí todo acceso con un lock reentrante (RLock: algunas funciones, como
# search_items, llaman internamente a otra función de este módulo que también
# toma el lock, y con un Lock normal eso sería un deadlock).
_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url      TEXT NOT NULL,
    extractor       TEXT NOT NULL,
    title           TEXT NOT NULL,
    uploader        TEXT,
    upload_date     TEXT,
    duration        INTEGER,
    media_type      TEXT NOT NULL DEFAULT 'video',
    file_path       TEXT NOT NULL,
    thumbnail_path  TEXT,
    filesize        INTEGER,
    ext             TEXT,
    tags            TEXT,
    description     TEXT,
    added_at        TEXT NOT NULL,
    raw_metadata    TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_extractor ON items(extractor);
CREATE INDEX IF NOT EXISTS idx_items_added_at ON items(added_at);

-- Trabajos de descarga: viven en la base de datos (no en memoria) para que
-- el progreso sobreviva a recargas de página y a reinicios del servidor.
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    audio_only  INTEGER NOT NULL DEFAULT 0,
    quality     TEXT NOT NULL DEFAULT 'best',
    state       TEXT NOT NULL DEFAULT 'en_cola',
    progress    REAL NOT NULL DEFAULT 0,
    message     TEXT,
    item_id     INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, uploader, description, tags, content='items', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, uploader, description, tags)
    VALUES (new.id, new.title, new.uploader, new.description, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, uploader, description, tags)
    VALUES ('delete', old.id, old.title, old.uploader, old.description, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, uploader, description, tags)
    VALUES ('delete', old.id, old.title, old.uploader, old.description, old.tags);
    INSERT INTO items_fts(rowid, title, uploader, description, tags)
    VALUES (new.id, new.title, new.uploader, new.description, new.tags);
END;
"""

_fts_enabled_cache: Optional[bool] = None


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: esta conexión se crea una vez por proceso y se
    # comparte entre el hilo de peticiones de FastAPI y los hilos worker de
    # descarga. Todo el acceso concurrente se serializa con _LOCK más abajo,
    # así que es seguro desactivar la comprobación de hilo único de sqlite3.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    # Con varias descargas simultáneas escribiendo su progreso, es mejor
    # esperar unos segundos que fallar con "database is locked".
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(conn: sqlite3.Connection) -> bool:
    """Crea las tablas si no existen. Devuelve True si FTS5 quedó activo."""
    global _fts_enabled_cache
    with _LOCK:
        conn.executescript(SCHEMA)
        _migrate_jobs_table(conn)
        try:
            conn.executescript(FTS_SCHEMA)
            _fts_enabled_cache = True
        except sqlite3.OperationalError:
            _fts_enabled_cache = False
        conn.commit()
        return bool(_fts_enabled_cache)


def _migrate_jobs_table(conn: sqlite3.Connection) -> None:
    """Añade columnas nuevas a `jobs` si faltan.

    `CREATE TABLE IF NOT EXISTS` no altera una tabla que ya existe, así que
    en una base de datos creada con una versión anterior de mediateca estas
    columnas simplemente no estarían — esto las agrega sin tocar los datos
    que ya hay.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    new_columns = {"format_id": "TEXT", "audio_format": "TEXT", "audio_bitrate": "TEXT"}
    for name, col_type in new_columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {col_type}")


def fts_enabled() -> bool:
    return bool(_fts_enabled_cache)


def insert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    fields = [
        "source_url", "extractor", "title", "uploader", "upload_date",
        "duration", "media_type", "file_path", "thumbnail_path", "filesize",
        "ext", "tags", "description", "added_at", "raw_metadata",
    ]
    values = []
    for f in fields:
        v = item.get(f)
        if f == "tags" and isinstance(v, (list, tuple)):
            v = json.dumps(list(v), ensure_ascii=False)
        if f == "raw_metadata" and isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        values.append(v)
    placeholders = ", ".join("?" for _ in fields)
    with _LOCK:
        cur = conn.execute(
            f"INSERT INTO items ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        # lastrowid es Optional según los stubs de sqlite3 (puede ser None
        # tras un INSERT que no genera fila, p.ej. en una vista), pero tras
        # un INSERT normal en una tabla con rowid como esta, siempre hay uno.
        assert cur.lastrowid is not None
        return cur.lastrowid


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    with _LOCK:
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def delete_item(conn: sqlite3.Connection, item_id: int) -> bool:
    with _LOCK:
        cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0


def list_items(
    conn: sqlite3.Connection,
    platform: Optional[str] = None,
    limit: int = 60,
    offset: int = 0,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM items"
    params: list[Any] = []
    if platform:
        query += " WHERE extractor = ?"
        params.append(platform)
    query += " ORDER BY added_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _LOCK:
        return conn.execute(query, params).fetchall()


def search_items(
    conn: sqlite3.Connection, query: str, limit: int = 60, offset: int = 0
) -> list[sqlite3.Row]:
    if not query.strip():
        return list_items(conn, limit=limit, offset=offset)

    if fts_enabled():
        with _LOCK:
            try:
                rows = conn.execute(
                    """
                    SELECT items.* FROM items
                    JOIN items_fts ON items.id = items_fts.rowid
                    WHERE items_fts MATCH ?
                    ORDER BY rank
                    LIMIT ? OFFSET ?
                    """,
                    (_fts_query(query), limit, offset),
                ).fetchall()
                return rows
            except sqlite3.OperationalError:
                pass  # cae al LIKE si la sintaxis MATCH falla (p.ej. caracteres raros)

    like = f"%{query}%"
    with _LOCK:
        return conn.execute(
            """
            SELECT * FROM items
            WHERE title LIKE ? OR uploader LIKE ? OR tags LIKE ? OR description LIKE ?
            ORDER BY added_at DESC
            LIMIT ? OFFSET ?
            """,
            (like, like, like, like, limit, offset),
        ).fetchall()


def _fts_query(query: str) -> str:
    # Escapa comillas y trata cada palabra como prefijo para búsqueda "as-you-type".
    tokens = [t.replace('"', '""') for t in query.strip().split()]
    return " ".join(f'"{t}"*' for t in tokens if t)


def count_items(conn: sqlite3.Connection) -> int:
    with _LOCK:
        row = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
        return int(row["c"])


def distinct_platforms(conn: sqlite3.Connection) -> list[str]:
    with _LOCK:
        rows = conn.execute(
            "SELECT DISTINCT extractor FROM items ORDER BY extractor"
        ).fetchall()
        return [r["extractor"] for r in rows]


# ---------------------------------------------------------------- jobs

def create_job(
    conn: sqlite3.Connection,
    job_id: str,
    url: str,
    audio_only: bool,
    quality: str,
    format_id: Optional[str] = None,
    audio_format: Optional[str] = None,
    audio_bitrate: Optional[str] = None,
) -> None:
    now = _now_iso()
    with _LOCK:
        conn.execute(
            """
            INSERT INTO jobs (id, url, audio_only, quality, format_id, audio_format,
                               audio_bitrate, state, progress, message, item_id,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'en_cola', 0, 'En cola…', NULL, ?, ?)
            """,
            (job_id, url, int(audio_only), quality, format_id, audio_format, audio_bitrate, now, now),
        )
        conn.commit()


def update_job(conn: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _LOCK:
        conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",
            (*fields.values(), job_id),
        )
        conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[sqlite3.Row]:
    with _LOCK:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    with _LOCK:
        return conn.execute(
            "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()


def mark_stale_jobs_interrupted(conn: sqlite3.Connection) -> int:
    """Al arrancar el servidor, cualquier job que haya quedado en un estado
    'en curso' es, por definición, de un proceso anterior que murió (un
    proceso recién iniciado no puede tener descargas activas todavía)."""
    with _LOCK:
        cur = conn.execute(
            """
            UPDATE jobs SET
                state = 'interrumpido',
                message = 'El servidor se reinició mientras esta descarga estaba en curso.',
                updated_at = ?
            WHERE state IN ('en_cola', 'descargando', 'procesando')
            """,
            (_now_iso(),),
        )
        conn.commit()
        return cur.rowcount


def _now_iso() -> str:
    # Microsegundos, no solo segundos: la lista de jobs se ordena por este
    # campo para mostrar "los tocados más recientemente" primero, y con
    # resolución de segundo dos actualizaciones seguidas del mismo job (algo
    # normal durante una descarga activa) podían quedar empatadas y
    # ordenarse de forma indefinida.
    return dt.datetime.now().isoformat(timespec="microseconds")
