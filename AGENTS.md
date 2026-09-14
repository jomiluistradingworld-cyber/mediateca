# mediateca — AGENTS.md

## Quick Reference

**Project**: Private, local multimedia downloader + library (Python 3.11+, SQLite, yt-dlp, FastAPI, Typer)
**Purpose**: Download from 1800+ sites, organize locally, search via SQLite FTS5, serve local web UI
**Entry point**: `mediateca` CLI (Typer) → `mediateca.cli:app`

---

## MCPs

Always use Context7 MCP when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.

## Commands

| Task | Command |
|------|---------|
| Install dev env | `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"` |
| Run CLI | `mediateca --help` |
| Download video | `mediateca download "URL" [--audio-only] [--quality 720]` |
| List library | `mediateca list [--platform youtube] [--limit 30]` |
| Search | `mediateca search "query"` |
| Item detail | `mediateca info <id>` |
| Remove item | `mediateca remove <id> [--delete-file]` |
| Reindexar disco | `mediateca rescan` (importa a la BD archivos físicos no indexados) |
| Web UI | `mediateca serve [--host 0.0.0.0] [--port 8420] [--reload]` |
| Lint | `ruff check .` |
| Type check | `mypy mediateca` |
| Tests | `pytest -q` |
| CI order | `ruff check . → mypy mediateca → pytest -q` |

---

## Architecture

```
mediateca/
├── config.py       # Config (TOML, ~/.config/mediateca/config.toml)
├── db.py           # SQLite schema + FTS5 search + thread-safe connection
├── downloader.py   # yt-dlp wrapper (build_ydl_opts, probe, download)
├── library.py      # Orchestrates config + db + downloader
├── models.py       # Pydantic models (API)
├── formatting.py   # Shared formatters (CLI + web)
├── cli.py          # Typer commands
└── web/
    ├── app.py          # FastAPI: pages + internal API + download workers
    ├── templates/      # Jinja2
    └── static/         # CSS/JS
```

**Layering**: config → db → downloader → library → interfaces (CLI, web)
**DB**: Single shared SQLite connection (thread-safe via lock in `db.py`)
**Jobs**: Persistent in `jobs` table (survives restart, browser close)

---

## Key Conventions

- **No network in tests**: `conftest.py` auto-mocks `downloader.download` and `yt_dlp.YoutubeDL`
- **Rich escaping**: All dynamic content → `esc()` before `console.print()` (brackets in titles)
- **Config paths**: Always `.expanduser()`; defaults created on first run
- **Cookies**: Stored separately at `~/.config/mediateca/cookies.txt` (chmod 600)
- **Media serving**: `/media` mount rebuilt per-request to reflect live `library_path` changes
- **Reindexar disco**: `library.sync_library()` escanea `library_path` e importa archivos no indexados (una pasada idempotente). Se lanza en hilo al arrancar el servidor, vía CLI `rescan` y desde Ajustes (`/api/rescan`).
- **Dedup**: `db.insert_item()` refresca el item existente (por `file_path`) en vez de crear una fila duplicada; `library.add_from_url()` salta la descarga si la `source_url` ya está en la BD.
- **Thread pool**: `ThreadPoolExecutor` sized by `config.concurrent_downloads`; recreated on config change

---

## Testing Gotchas

- `conftest.py` fixtures isolate FS (`tmp_path`) and monkeypatch config dirs
- `_no_real_downloads` autouse fixture blocks accidental HTTP calls
- `test_build_opts_*` cover yt-dlp option builder logic
- `test_probe_*` use `_FakeYDL` class with canned responses
- DB tests use real SQLite in `tmp_path`; migration test simulates old schema
- Concurrency test hammers shared connection from 8 threads

---

## Config (config.example.toml)

```
library_path = "~/Mediateca"
db_path = "~/.local/share/mediateca/mediateca.db"
default_quality = "best"
default_audio_format = "mp3"
download_thumbnails = true
concurrent_downloads = 2
sleep_interval = 0
max_sleep_interval = 0
rate_limit_kbps = 0
concurrent_fragments = 1
embed_metadata = true
host = "127.0.0.1"
port = 8420
```

---

## Development Notes

- **Python**: 3.11+ (type hints pervasive; mypy permissive: `ignore_missing_imports = true`, `disable_error_code = ["import-untyped"]`)
- **Ruff**: line-length 100, ignores E501 (formatter handles it)
- **FTS5**: Used if SQLite supports it; falls back to LIKE search
- **Download resume**: HTTP range requests; progress persisted in DB
- **Pausable downloads**: Worker checks `cancel_event`; state → "pausado"
- **SSE streaming**: `/api/jobs/{id}/stream` replaces polling
- **No auth**: Designed for local-only (127.0.0.1); add basic auth if exposing
- **Embed metadata (thumbnails in audio)**: Requires `mutagen` in the project's `.venv` (not system Python). yt-dlp's `EmbedThumbnail` post-processor imports from `yt_dlp.dependencies.mutagen`. If missing → `ERROR: module mutagen was not found`. Fix: `.venv/bin/pip install mutagen` + restart server.

---

## Extending

- Add extractor options → `downloader.build_ydl_opts()`
- New API endpoints → `web/app.py` (follow existing patterns)
- DB migrations → `db.init_db()` (adds missing columns idempotently)
- CLI commands → `cli.py` (Typer, reuse `library.*` functions)
