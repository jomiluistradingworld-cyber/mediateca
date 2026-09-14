# Auditoría técnica de mediateca — hacia nivel experto

Repo revisado: `jomiluistradingworld-cyber/mediateca` (commit `fecc5f0`), después del fix de la conexión SQLite compartida.

---

## 🔴 Bugs reales (no cosméticos)

### 1. "Descargas simultáneas" en Ajustes no hace nada
`web/app.py`, `create_app()`:
```python
app.state.executor = ThreadPoolExecutor(max_workers=max(1, config.concurrent_downloads))
```
El `ThreadPoolExecutor` se crea **una sola vez** al arrancar. Cuando cambias
"Descargas simultáneas" en `/settings`, se guarda en `config.toml` y en
`app.state.config`, pero el executor ya existente nunca cambia de tamaño
(`ThreadPoolExecutor` no soporta redimensionarse en caliente). El ajuste
queda guardado pero es un placebo hasta que reinicias el servidor a mano —
y la propia página solo avisa de esto para host/puerto, no para este campo.

**Arreglo:** o bien reconstruir el executor al guardar ajustes (cerrando el
viejo con `wait=False` y creando uno nuevo), o simplemente documentar en la
UI que este campo también requiere reinicio, igual que host/puerto.

### 2. Cambiar la carpeta de la biblioteca rompe `/media` hasta reiniciar
```python
app.mount("/media", StaticFiles(directory=str(config.library_path)), name="media")
```
Este mount se fija con el `library_path` que había **al crear la app**. Si
cambias la carpeta en Ajustes, los archivos nuevos se descargan ahí, pero
`/media` sigue sirviendo desde la ruta vieja → los ítems nuevos dan 404 al
reproducirlos hasta reiniciar el proceso. Incluso tras reiniciar, los ítems
ya guardados con la ruta relativa antigua quedan huérfanos si de verdad
moviste la carpeta (esto es esperable; lo que no lo es es el silencio del
`/media` roto mientras tanto).

**Arreglo:** mismo aviso de "requiere reinicio" en la UI, o (mejor, y no es
mucho código) montar `/media` apuntando a una ruta dinámica leída de
`app.state.config` en cada request en vez de capturarla una vez.

### 3. La biblioteca web nunca muestra más de 60 elementos
```python
@app.get("/api/library")
def api_library(q: Optional[str] = None, platform: Optional[str] = None, limit: int = 60):
    ...
    rows = library.list_library(conn, platform=platform, limit=limit)  # sin offset
```
`db.list_items()` sí soporta `offset`, pero ni `api_library`, ni
`library.list_library`, ni `library.search_library` / `db.search_items` lo
exponen. El frontend tampoco tiene paginación ni "cargar más". Resultado:
en cuanto tu biblioteca pase de 60 elementos (el objetivo mismo de la app:
acumular una colección con el tiempo), todo lo más antiguo se vuelve
invisible en la web — sigue en la base de datos y accesible por `mediateca
list -n 500`, pero no desde el navegador.

**Arreglo:** añadir `offset` a `search_items`/`search_library`/`api_library`
y un botón "Cargar más" (o scroll infinito) en `app.js`. Es una pieza
importante para que la app "aguante" a largo plazo.

### 4. `JobStatus` (Pydantic) desincronizado de los estados reales
`models.py`:
```python
state: Literal["en_cola", "descargando", "procesando", "listo", "error"]
```
Faltan `"pausado"` e `"interrumpido"`, que sí existen en `db.py`
(`RESUMABLE_STATES`) y en `app.js`. Hoy no revienta nada porque
`api_job`/`api_jobs_list` devuelven dicts crudos sin validar contra este
modelo — pero es deuda técnica esperando a explotar en cuanto alguien lo
use para validar respuestas o generar el schema de OpenAPI (`/docs`
actualmente miente sobre los estados posibles). Arreglar el `Literal` y, ya
puestos, usar `response_model=JobStatus` en los endpoints de jobs para que
FastAPI valide y documente de verdad.

---

## 🟠 Seguridad

### 5. Sin validar el esquema de la URL de descarga
`models.py`: `url: str` sin restricción. Dos consecuencias:
- **Auto-XSS de bajo riesgo:** `item.html` renderiza
  `<a href="{{ item.source_url }}">` sin sanitizar el esquema. El
  autoescape de Jinja2 protege contra comillas/HTML, pero no contra un
  esquema `javascript:` en un atributo `href`. Como el dato lo introdujiste
  tú mismo al pedir la descarga, es "auto-XSS" (bajo impacto), pero es
  gratis cerrarlo.
- **SSRF si algún día expones el host:** el extractor genérico de yt-dlp
  puede intentar pedir cualquier URL. Mientras uses `127.0.0.1` no importa,
  pero si algún día pones `host = "0.0.0.0"` en `config.toml` (la propia
  app lo permite) cualquiera en tu red podría pedirle a tu servidor que
  "descargue" `http://169.254.169.254/...` o una IP interna.

**Arreglo:** validar con Pydantic que `url` empiece por `http://` o
`https://` (un `field_validator` de 3 líneas), y de paso usar
`urllib.parse` para eso mismo antes de pasarlo a yt-dlp.

### 6. Cero autenticación
Totalmente razonable para uso 100% local en tu ThinkPad, pero si alguna vez
expones el puerto 8420 a tu red doméstica (por ejemplo para verlo desde el
móvil), cualquier dispositivo en esa red puede borrar tu biblioteca o
lanzar descargas. Nivel experto no significa "añade OAuth" para un
proyecto personal, pero sí vale la pena un aviso claro en el README ("no
expongas esto fuera de localhost sin un proxy con auth delante") o, si te
interesa, una autenticación HTTP Basic mínima protegida por variable de
entorno, opcional.

---

## 🟡 Fiabilidad / arquitectura (lo que ya teníamos en el radar)

- **Polling → SSE.** Ahora que la conexión ya no revienta, esto deja de ser
  una urgencia y pasa a ser una mejora de eficiencia: cada descarga activa
  hace 1 request/segundo por pestaña abierta. Con SSE sería una sola
  conexión persistente por pestaña, sin importar cuántos jobs corran.
- **`sleep_interval` / `ratelimit` en yt-dlp.** `downloader.py` no limita
  ritmo de peticiones. Añadir `sleep_interval`, `max_sleep_interval` y
  `ratelimit` (configurables desde Ajustes) reduce el riesgo de bloqueo por
  sitios como YouTube en descargas masivas.
- **Distinguir 429/403 de un corte de red normal.** Hoy, cualquier `status
  == "error"` del hook de progreso se traduce como "Corte de red,
  reintentando automáticamente…" — incluso si en realidad es un bloqueo del
  sitio (429/403) que *no* se va a arreglar solo reintentando. Vale la pena
  inspeccionar el mensaje de error de yt-dlp y mostrar algo más honesto
  ("Este sitio está bloqueando las descargas, prueba más tarde") en ese
  caso, en vez de prometer un reintento automático que no va a servir de
  nada.
- **Cierre del executor al apagar.** El `shutdown` que añadimos cierra la
  conexión SQLite pero no el `ThreadPoolExecutor`; con descargas activas,
  `Ctrl+C` puede tardar en devolver el control. La recuperación al
  reiniciar (`mark_stale_jobs_interrupted`) ya cubre la corrección de
  datos, así que esto es solo una mejora de experiencia, no un bug.

---

## 🟢 Calidad de código y mantenibilidad

- **Cero tests, cero CI.** No hay ni un solo test ni workflow de GitHub
  Actions. Para un proyecto que ya tiene lógica no trivial (FTS5 con
  fallback, reanudación de descargas, estados de jobs), unos tests de
  `pytest` sobre `db.py` (CRUD + búsqueda) y un `TestClient` de FastAPI
  sobre los endpoints principales darían mucha confianza para seguir
  tocando código sin miedo a romper algo. Es, con diferencia, lo que más
  "nivel experto" le falta al repo ahora mismo.
- **Sin lint ni type-checking en CI.** El código ya usa type hints en casi
  todas partes (buen hábito existente) — añadir `ruff` (lint + format) y
  `mypy`/`pyright` en un workflow simple de GitHub Actions sería barato y
  de alto valor, aprovechando ese trabajo ya hecho.
- **Sin logging estructurado.** Hoy solo existen los logs de acceso de
  uvicorn. Un `logging.getLogger("mediateca")` con handler a archivo
  rotativo en `~/.local/share/mediateca/mediateca.log` habría hecho mucho
  más fácil diagnosticar el problema de conexiones sin depender de que
  guardaras la consola completa.
- **La CLI no cierra su conexión SQLite** (`cli.py`, cada comando abre con
  `library.open_library()` y no la cierra). Inofensivo porque el proceso
  termina enseguida, pero inconsistente con la higiene que ya aplicamos en
  la web; un `try/finally: conn.close()` o un context manager lo deja
  prolijo.

---

## Priorización sugerida

| Prioridad | Ítem | Esfuerzo |
|---|---|---|
| Alta | #3 paginación de biblioteca | Bajo |
| Alta | #1 y #2 ajustes que no aplican en caliente | Bajo |
| Alta | #5 validar esquema de URL | Muy bajo |
| Media | Tests + CI básica | Medio |
| Media | SSE en vez de polling | Medio |
| Baja | Rate-limit / detección 429-403 en yt-dlp | Bajo-medio |
| Baja | #4 `JobStatus` Literal, logging, cierre de conexión en CLI | Muy bajo |
