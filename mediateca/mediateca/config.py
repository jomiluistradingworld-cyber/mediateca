"""Carga y persistencia de la configuración de mediateca.

La configuración vive en ~/.config/mediateca/config.toml. Si no existe,
se crea con valores por defecto sensatos la primera vez que se usa
cualquier comando.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "mediateca"

DEFAULTS: dict = {
    "library_path": "~/Mediateca",
    "db_path": "~/.local/share/mediateca/mediateca.db",
    "default_quality": "best",
    "default_audio_format": "mp3",
    "download_thumbnails": True,
    "concurrent_downloads": 2,
    "host": "127.0.0.1",
    "port": 8420,
}


@dataclass
class Config:
    library_path: Path
    db_path: Path
    default_quality: str
    default_audio_format: str
    download_thumbnails: bool
    concurrent_downloads: int
    host: str
    port: int

    @property
    def config_path(self) -> Path:
        return get_config_dir() / "config.toml"


def get_config_dir() -> Path:
    d = Path.home() / ".config" / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _expand(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def _write_default_config(path: Path) -> None:
    lines = [
        "# Configuración de mediateca. Ver config.example.toml para más detalle.",
        "",
    ]
    for key, value in DEFAULTS.items():
        lines.append(_toml_line(key, value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_line(key: str, value) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        rendered = str(value)
    else:
        escaped = str(value).replace('"', '\\"')
        rendered = f'"{escaped}"'
    return f"{key} = {rendered}"


def load_config() -> Config:
    path = get_config_dir() / "config.toml"
    if not path.exists():
        _write_default_config(path)

    with path.open("rb") as f:
        raw = tomllib.load(f)

    merged = {**DEFAULTS, **raw}

    return Config(
        library_path=_expand(merged["library_path"]),
        db_path=_expand(merged["db_path"]),
        default_quality=str(merged["default_quality"]),
        default_audio_format=str(merged["default_audio_format"]),
        download_thumbnails=bool(merged["download_thumbnails"]),
        concurrent_downloads=int(merged["concurrent_downloads"]),
        host=str(merged["host"]),
        port=int(merged["port"]),
    )


def save_config(config: Config) -> None:
    """Guarda la config actual de vuelta al archivo TOML."""
    data = asdict(config)
    lines = ["# Configuración de mediateca.", ""]
    for f in fields(config):
        key = f.name
        value = data[key]
        if isinstance(value, Path):
            value = str(value)
        lines.append(_toml_line(key, value))
    config.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Asegurar que las carpetas dependientes existen tras un cambio de ruta.
    config.library_path.mkdir(parents=True, exist_ok=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dirs(config: Config) -> None:
    config.library_path.mkdir(parents=True, exist_ok=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":  # pragma: no cover - utilidad manual
    cfg = load_config()
    print(f"Config cargada desde {cfg.config_path}", file=sys.stderr)
    print(cfg)
