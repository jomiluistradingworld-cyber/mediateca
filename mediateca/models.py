"""Modelos de datos compartidos por la API web."""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Estados posibles de un job, en un solo sitio para que este modelo no se
# desincronice de los que realmente usan db.py/app.py/app.js (antes faltaban
# "pausado" e "interrumpido" aquí, así que /docs documentaba estados que en
# la práctica nunca se producían y silenciaba dos que sí).
JobState = Literal[
    "en_cola", "descargando", "procesando", "listo", "error", "pausado", "interrumpido"
]

_ALLOWED_URL_SCHEMES = ("http", "https")


def _check_url_scheme(v: str) -> str:
    v = v.strip()
    parsed = urlparse(v)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ValueError(
            "La URL debe empezar por http:// o https:// e incluir un dominio válido."
        )
    return v


class DownloadRequest(BaseModel):
    url: str
    audio_only: bool = False
    quality: str = "best"
    # Si vienen de la vista previa (el usuario eligió un formato exacto en
    # /download antes de confirmar), mandan sobre quality/audio_only.
    format_id: Optional[str] = None
    audio_format: Optional[str] = None
    audio_bitrate: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _validar_esquema_url(cls, v: str) -> str:
        return _check_url_scheme(v)


class ProbeRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _validar_esquema_url(cls, v: str) -> str:
        return _check_url_scheme(v)


class ProbeFormat(BaseModel):
    format_id: Optional[str] = None
    ext: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[float] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    filesize: Optional[int] = None
    format_note: Optional[str] = None


class ProbeEntry(BaseModel):
    url: str
    title: str
    duration: Optional[int] = None
    thumbnail: Optional[str] = None


class ProbeResult(BaseModel):
    is_playlist: bool
    title: str
    uploader: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    entry_count: Optional[int] = None
    entries: list[ProbeEntry] = Field(default_factory=list)
    formats: list[ProbeFormat] = Field(default_factory=list)


class JobStatus(BaseModel):
    job_id: str
    url: str
    audio_only: bool = False
    quality: str = "best"
    format_id: Optional[str] = None
    audio_format: Optional[str] = None
    audio_bitrate: Optional[str] = None
    state: JobState
    progress: float = 0.0
    message: str = ""
    item_id: Optional[int] = None


class MediaItem(BaseModel):
    id: int
    source_url: str
    extractor: str
    title: str
    uploader: Optional[str] = None
    upload_date: Optional[str] = None
    duration: Optional[int] = None
    media_type: str
    file_path: str
    thumbnail_path: Optional[str] = None
    filesize: Optional[int] = None
    ext: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    added_at: str
