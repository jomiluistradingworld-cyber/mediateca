"""Configuración de logging para mediateca.

Antes, lo único que quedaba registrado era la salida de acceso de uvicorn
en la terminal: para diagnosticar un fallo había que haber guardado la
consola completa a mano (así se encontró el problema de conexiones SQLite
que motivó esta ronda de mejoras). Este módulo añade un log persistente
y rotativo junto a la base de datos, para que quede rastro incluso si
nadie estaba mirando la terminal en el momento del fallo.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOGGER_NAME = "mediateca"


def configure_logging(log_dir: Path) -> logging.Logger:
    """Configura (una sola vez) y devuelve el logger de mediateca.

    `log_dir` es normalmente la carpeta de la base de datos
    (~/.local/share/mediateca/), así el log queda junto a los demás datos
    de la app. Es seguro llamar a esta función más de una vez (p. ej. con
    `--reload`): si el logger ya tiene handlers configurados, no los duplica.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "mediateca.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    return logger
