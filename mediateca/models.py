"""Modelos de datos compartidos por la API web."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DownloadRequest(BaseModel):
    url: str
    audio_only: bool = False
    quality: str = "best"


class JobStatus(BaseModel):
    job_id: str
    url: str
    state: Literal["en_cola", "descargando", "procesando", "listo", "error"]
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
