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


class DownloadRequest(BaseModel):
    url: str
    audio_only: bool = False
    quality: str = "best"

    @field_validator("url")
    @classmethod
    def _validar_esquema_url(cls, v: str) -> str:
        v = v.strip()
        parsed = urlparse(v)
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            raise ValueError(
                "La URL debe empezar por http:// o https:// e incluir un dominio válido."
            )
        return v


class JobStatus(BaseModel):
    job_id: str
    url: str
    audio_only: bool = False
    quality: str = "best"
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
