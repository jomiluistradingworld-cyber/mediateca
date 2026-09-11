# mediateca

Descargador multimedia universal, privado y 100% local. Todo lo que
descargas se queda en tu disco, organizado y con metadatos indexados en una
base de datos SQLite propia, sin cuentas ni servicios de terceros de por
medio.

Pensado para correr cómodo en tu ThinkPad T480 (i5-8250U, 8 GB RAM): sin
Electron, sin Node, sin base de datos externa. Solo Python + SQLite +
ffmpeg.

## Qué incluye esta primera versión

- **Motor de descarga** sobre [yt-dlp](https://github.com/yt-dlp/yt-dlp),
  que soporta más de 1800 sitios (YouTube, Vimeo, SoundCloud, Twitter/X,
  Twitch, podcasts, etc.).
- **Biblioteca local** organizada automáticamente en
  `plataforma/autor/título.ext`, con miniatura descargada localmente.
- **Base de datos SQLite** con búsqueda de texto completo (FTS5 si tu
  SQLite lo soporta; si no, cae automáticamente a búsqueda simple).
- **CLI** (`mediateca download`, `list`, `search`, `info`, `remove`, `serve`).
- **Interfaz web local** (`mediateca serve`) para navegar la biblioteca,
  buscar, lanzar descargas con progreso en vivo y reproducir lo descargado
  desde el navegador, sin salir de tu red local.
- **Descargas persistentes, pausables y reanudables**: el progreso vive en
  la base de datos (no en la pestaña del navegador), así que sobrevive a
  cambiar de pestaña, cerrar el navegador o incluso reiniciar el servidor.
  Puedes pausar una descarga en curso y reanudarla más tarde exactamente
  desde donde se quedó (usa rangos HTTP, no vuelve a bajar lo ya descargado).

## Requisitos

- Python 3.11 o superior (`python3 --version`)
- ffmpeg (necesario para fusionar video+audio y convertir formatos)

En Ubuntu:

```bash
sudo apt update
sudo apt install python3-venv ffmpeg
```

## Instalación

```bash
cd mediateca
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Esto instala el paquete en modo editable y crea el comando `mediateca` en
tu entorno virtual.

## Uso rápido

```bash
# Descargar un video (calidad máxima por defecto)
mediateca download "https://ejemplo.com/video"

# Solo el audio, en mp3
mediateca download "https://ejemplo.com/video" --audio-only

# Limitar la calidad (útil para ahorrar espacio/CPU en el T480)
mediateca download "https://ejemplo.com/video" --quality 720

# Ver lo que llevas guardado
mediateca list

# Buscar en tu biblioteca
mediateca search "nombre o tema"

# Detalle de un elemento (usa el ID que muestra 'list' o 'search')
mediateca info 3

# Levantar la interfaz web local (por defecto http://127.0.0.1:8420)
mediateca serve
```

La primera vez que ejecutas cualquier comando se crea automáticamente
`~/.config/mediateca/config.toml` con valores por defecto (biblioteca en
`~/Mediateca`, base de datos en `~/.local/share/mediateca/`). Puedes
editarlo a mano o desde la pestaña **Ajustes** de la web.

## Estructura del proyecto

```
mediateca/
├── mediateca/
│   ├── config.py       # carga/guarda config.toml
│   ├── db.py           # esquema SQLite + búsqueda (FTS5 con fallback)
│   ├── downloader.py   # envoltorio sobre yt-dlp
│   ├── library.py      # orquesta config + db + downloader
│   ├── models.py       # modelos Pydantic de la API
│   ├── formatting.py   # helpers de formato compartidos (CLI + web)
│   ├── cli.py          # comandos Typer
│   └── web/
│       ├── app.py          # FastAPI: páginas + API interna
│       ├── templates/      # Jinja2
│       └── static/         # CSS/JS
└── config.example.toml
```

La arquitectura está deliberadamente separada en capas (config → db →
downloader → library → interfaces) para que sea fácil añadir features sin
tocar todo lo demás.

## Hoja de ruta sugerida (para cuando quieras subir de nivel)

Este MVP prioriza simplicidad. Ideas para ir hacia "experto" cuando quieras:

- **Playlists y canales completos**: hoy `noplaylist=True` descarga solo un
  item; se puede añadir un modo playlist con barra de progreso agregada.
- **Monitoreo de canales**: guardar canales "seguidos" y una tarea en segundo
  plano que revise y descargue lo nuevo (cron o `apscheduler`).
- **Etiquetado manual y colecciones/playlists propias** en la web.
- **Deduplicación** por hash o por `id` de extractor antes de descargar de
  nuevo una URL ya existente.
- **Embeber miniatura y metadatos** dentro del propio archivo de audio
  (mutagen) para que se vea bien en cualquier reproductor.
- **Exportar/backup** de la base de datos y migrarla a otra máquina.
- **Autenticación básica** en la web si algún día la expones fuera de
  `127.0.0.1` (hoy está pensada solo para uso local).

